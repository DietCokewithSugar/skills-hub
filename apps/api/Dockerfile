# Bench 后端镜像：API + worker 跑在同一个容器里。
#
# 为什么两个进程同容器：用户上传的文件由 API 进程写进
# BENCH_WORKSPACE_ROOT（routes.py 的 upload_input），再由 worker 进程读出来
# 交给沙箱（loop.py 的 build_context）。两者必须看到同一个文件系统。
# 拆成两个 Render service 会让 worker 读不到刚上传的文件，执行直接失败。
#
# 构建上下文是**仓库根目录**，不是 apps/api ——
# 容器里的 /app 对应仓库根，这样 config.py 的
# `_REPO_ROOT = Path(__file__).resolve().parents[3]` 解析结果与本地一致。

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# libreoffice-writer：R6 的 Word → PDF 预览。只装 libreoffice-core 是不够的，
#   没有 Writer 过滤器就加载不了 .docx（报 source file could not be loaded）。
# fonts-noto-cjk：报告是中文的，缺 CJK 字体转出来的 PDF 会是一片方框。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer \
      fonts-noto-cjk \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/api /app/apps/api
COPY skills /app/skills
COPY sandbox/runtime /app/sandbox/runtime

# CI 用 --build-arg INSTALL_DEV=true 构一个带 pytest 的变体，
# 以便在**和生产完全相同的镜像**里跑 PDF 转换测试。
# 生产镜像保持 false，不带测试依赖。
ARG INSTALL_DEV=false

# 必须是 editable 安装：config.py 用 `Path(__file__).parents[3]` 推仓库根，
# 装进 site-packages 的话这个路径会指到 site-packages 的上层，
# skills_dir / workspace_root 的默认值就全错了。
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      pip install --no-cache-dir -e "/app/apps/api[dev]"; \
    else \
      pip install --no-cache-dir -e /app/apps/api; \
    fi
COPY apps/api/docker-entrypoint.sh /usr/local/bin/bench-entrypoint
RUN chmod +x /usr/local/bin/bench-entrypoint

# 非 root 运行。/data 是 Render 持久盘的挂载点，需要可写。
RUN useradd --create-home --uid 10001 bench \
 && mkdir -p /data \
 && chown -R bench:bench /app /data
USER bench

ENV BENCH_SKILLS_DIR=/app/skills \
    BENCH_WORKSPACE_ROOT=/data/workspaces \
    PORT=8000

EXPOSE 8000

CMD ["bench-entrypoint"]
