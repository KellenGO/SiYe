import os
import subprocess

ROOT = r"C:\Users\Kellen\Desktop\MediaCrawler-main"
for name in [".tmp_z.txt", ".tmp_dy_spike.py", ".tmp_dy_spike.txt"]:
    p = os.path.join(ROOT, name)
    if os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass

MSG = """docs: 热搜榜方案补充抖音 spike 实测结果与入口取舍

- 抖音热榜实测可用：iesdouyin 公开榜单接口不需要登录、不需要签名、不需要浏览器，
  返回 50 条 {word, hot_value, label} 与列表更新时间；web 端 hot/search/list 带不带
  a_bogus 都是 200 + 空 body，已排除。
- 记录榜单是分钟级更新，缓存 TTL 定为 5 分钟量级。
- 明确数据层按平台返回、聚合只在展示层；并记下入口 A（搜索框推荐词）的语义错位问题：
  热榜前几名多为时政新闻，而推荐搜索位是兴趣向语义。
"""

out = []
r = subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True, text=True)
out.append("add rc=%s" % r.returncode)
r = subprocess.run(["git", "commit", "-F", "-"], cwd=ROOT, input=MSG, capture_output=True, text=True)
out.append("commit rc=%s\n%s%s" % (r.returncode, r.stdout, r.stderr))
token = subprocess.run(["gh", "auth", "token"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
r = subprocess.run(["git", "push", "https://%s@github.com/KellenGO/SiYe.git" % token, "master"],
                   cwd=ROOT, capture_output=True, text=True)
out.append("push rc=%s\n%s%s" % (r.returncode, r.stdout, r.stderr))
r = subprocess.run(["git", "ls-remote", "https://github.com/KellenGO/SiYe.git", "refs/heads/master"],
                   cwd=ROOT, capture_output=True, text=True)
out.append("remote: " + r.stdout)
r = subprocess.run(["git", "status", "--porcelain=v1"], cwd=ROOT, capture_output=True, text=True)
out.append("status: " + (r.stdout or "(clean)"))

with open(os.path.join(ROOT, ".tmp_out.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(out))
