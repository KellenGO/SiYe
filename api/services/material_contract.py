"""Shared limits and public source links for platform material snapshots."""

from urllib.parse import urlsplit, urlunsplit

MAX_SECTION_CHARS = 256000

def public_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class MaterialAccess:
    def __init__(self, materials):
        self.materials = {item["key"]: item for item in materials}
        self.citations = {key: f"S{index}" for index, key in enumerate(self.materials, 1)}
        self.read = {key: {name: set() for name in SECTIONS} for key in self.materials}
        self.parts, self.sections = {}, {}
        for key, item in self.materials.items():
            self.parts[key], self.sections[key] = {}, {}
            for name in SECTIONS:
                value = item[name]
                chunks, limited = section_chunks(value, name)
                self.parts[key][name] = chunks
                self.sections[key][name] = {field: value.get(field) for field in ("state", "reason", "truncated")}
                self.sections[key][name].update(chunks=len(chunks), count=len(value.get("entries", [])),
                    truncated=bool(value.get("truncated") or limited),
                    content_limited=limited,
                    collection_truncated=bool(value.get("truncated") or limited))
                if limited:
                    self.sections[key][name]["reason"] = "内容超过单项读取上限，保留前部内容"
                if name == "subtitles":
                    self.sections[key][name]["metadata"] = value.get("metadata", {})

    def manifest(self):
        return [{"key": key, "citation": self.citations[key], "title": item["title"], "platform": item["platform"],
                 "snippet": item.get("snippet", "")[:240],
                 "chunks": sum(len(chunks) for chunks in self.parts[key].values()),
                 "url": item["url"], "sections": {name: dict(value) for name, value in self.sections[key].items()}}
                for key, item in self.materials.items()]

    def resolve(self, key):
        if not isinstance(key, str):
            raise ToolInputError("key 必须是本轮清单中的 S1/W1 等标识")
        key = next((identity for identity, citation in self.citations.items() if citation == key), key)
        if key not in self.materials:
            raise ToolInputError("来源不存在；使用本轮清单中的公开标识，如 S1")
        return key

    def chunk(self, key, section, index):
        key = self.resolve(key)
        if section not in SECTIONS or type(index) is not int:
            raise ToolInputError("section 必须为 body/comments/subtitles，index 必须为从 0 开始的整数")
        chunks = self.parts[key][section]
        if index < 0 or index >= len(chunks):
            raise ToolInputError("资料分段不存在；index 必须小于该 section 的 chunks")
        self.read[key][section].add(index)
        return chunks[index]

    def coverage(self):
        output = []
        for row in self.manifest():
            for name, value in row["sections"].items():
                indices = sorted(self.read[row["key"]][name])
                value.update(read_chunks=len(indices), read_indices=indices,
                    complete=len(indices) == value["chunks"],
                    reading_complete=bool(value["chunks"]) and len(indices) == value["chunks"])
            count = sum(value["read_chunks"] for value in row["sections"].values())
            output.append({**row, "read_chunks": count, "complete": bool(count) and count == row["chunks"]})
        return output

    def model_coverage(self):
        rows = self.coverage()
        for row in rows:
            row["key"] = row["citation"]
            row.pop("complete")
            for value in row["sections"].values():
                value.pop("complete")
                value.pop("truncated")
        return rows

