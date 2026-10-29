"""만든 페이지(site/)를 GitHub의 gh-pages 가지로 올린다.

수집과 페이지 생성은 이 PC에서 하고, GitHub Pages는 올라온 페이지를 보여 주기만 한다.
올릴 내용은 .publish/ 폴더(gh-pages 전용 저장소)에 복사해 두고 바뀐 것이 있을 때만 커밋한다.
"""
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site"
PUB = ROOT / ".publish"
BRANCH = "gh-pages"
AUTHOR = ["-c", "user.name=hufsiei", "-c", "user.email=hufsiei@users.noreply.github.com"]
# 예약 실행 중에는 로그인 창을 띄우지 않고 바로 실패하게 한다
ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def git(*args, cwd=PUB, check=True):
    return subprocess.run(["git", *AUTHOR, *args], cwd=cwd, env=ENV, check=check,
                          capture_output=True, text=True, encoding="utf-8")


def _remote():
    return git("remote", "get-url", "origin", cwd=ROOT).stdout.strip()


def _prepare():
    if (PUB / ".git").exists():
        return
    PUB.mkdir(exist_ok=True)
    git("init", "-q")
    git("remote", "add", "origin", _remote())
    # 원격에 gh-pages가 있으면 이어 쓰고, 없으면 빈 가지로 시작한다
    if git("fetch", "-q", "origin", BRANCH, check=False).returncode == 0:
        git("checkout", "-q", "-B", BRANCH, f"origin/{BRANCH}")
    else:
        git("checkout", "-q", "--orphan", BRANCH)


def publish(log=print):
    try:
        _prepare()
        for p in PUB.iterdir():
            if p.name != ".git":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        shutil.copytree(SITE, PUB, dirs_exist_ok=True)
        (PUB / ".nojekyll").touch()  # GitHub Pages가 파일을 가공하지 않게
        git("add", "-A")
        if git("status", "--porcelain").stdout.strip():
            git("commit", "-q", "-m", "공지 업데이트")
        # 지난번에 올리지 못한 커밋도 함께 올린다
        pushed = git("rev-parse", "-q", "--verify", f"origin/{BRANCH}", check=False).stdout.strip()
        if pushed == git("rev-parse", "HEAD").stdout.strip():
            log("게시: 바뀐 내용 없음")
            return
        git("push", "-q", "-u", "origin", BRANCH)
        log("게시: GitHub Pages로 올림")
    except subprocess.CalledProcessError as e:  # 올리기가 실패해도 페이지 생성 결과는 남는다
        log(f"게시 실패: {(e.stderr or e.stdout or '').strip()[:300]}")


if __name__ == "__main__":
    publish()
