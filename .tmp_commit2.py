import os
import subprocess

ROOT = r"C:\Users\Kellen\Desktop\MediaCrawler-main"

for name in [".tmp_gitfix.py", ".tmp_gitfix.txt", ".tmp_gitfix2.py", ".tmp_gitfix2.txt",
             ".tmp_gitfix3.py", ".tmp_gitfix3.txt", ".tmp_gitfix4.py", ".tmp_gitfix4.txt",
             ".tmp_pack.py", ".tmp_probe.py", ".tmp_probe2.py", ".tmp_probe3.py",
             ".tmp_sonner.txt", ".tmp_build.txt", ".tmp_state.py"]:
    p = os.path.join(ROOT, name)
    if os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass

MSG = """fix: 去掉重复 toast、首页保留搜索状态，并完成 git 修复

- 全局只保留一个 sonner Toaster：它的每个实例都会渲染所有位置的分节，
  之前挂两个导致每条 toast 画两遍（登录提醒中间一份、右上角一份）。
  需要顶部居中的提示改为在 toast 上显式传 position: "top-center"。
- 未登录平台提醒固定为顶部居中蓝色样式，右上角不再重复出现。
- 搜索失败提醒改为每轮只弹一次：去重集合提升到模块级，
  切页返回导致 SearchPage 重新挂载、任务恢复时不再重复弹窗。
- 返回「首页」保留搜索状态：有本轮结果时不再重置成初始首页大搜索框。
- git 修复：清掉丢失数据后遗留的孤儿 .idx 与过期 multi-pack-index、
  删除已丢分支的失效 reflog；从远端补回对象并重建 refactor-round-2 分支引用；
  手工重建被 prune 误删的 side-tasks worktree 管理目录并 reset --mixed 重建索引。
  fsck 恢复干净，支线工作区只剩一处原有的文档改动。
"""

r = subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True, text=True)
out = ["add rc=%s" % r.returncode]
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

with open(os.path.join(ROOT, ".tmp_commit2.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(out))
