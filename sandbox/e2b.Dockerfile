# Bench 沙箱镜像（E2B template）。
#
# PRD 6.4：「镜像按 skill 的 requirements.txt 预构建并打 tag，运行时禁止
# 安装依赖 —— 这既是安全要求，也是保证结果可复现的要求（依赖版本漂移会让
# 同样的输入算出不同的数）。」
#
# 构建：
#   e2b template build -c sandbox/e2b.Dockerfile -n bench-python-312
#
# 出网不在这里配置：runner 建沙箱时传 allow_internet_access=False，
# 白名单由 skill.yaml 的 limits.network_allowlist 决定。

FROM e2bdev/code-interpreter:latest

# 依赖版本全部钉死 —— 浮动版本会让 R0.5 的回归测试失去意义
RUN pip install --no-cache-dir \
      pandas==2.2.3 \
      numpy==2.1.3 \
      openpyxl==3.1.5 \
      matplotlib==3.9.2 \
      docxtpl==0.18.0 \
      python-docx==1.1.2 \
      scipy==1.14.1

# 受信任的计算库（R0.7）。模型被提示优先调用这些，而不是自己重写统计逻辑。
COPY runtime/bench /usr/local/lib/python3.12/site-packages/bench

# 契约目录
RUN mkdir -p /workspace /output /meta /workspace/scratch \
 && chmod 777 /workspace /output /meta

ENV BENCH_WORKSPACE=/workspace \
    BENCH_OUTPUT=/output \
    BENCH_META=/meta \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg

WORKDIR /workspace
