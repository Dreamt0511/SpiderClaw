FROM ubuntu:22.04

# 换国内源（加速 apt-get）
RUN sed -i 's/archive.ubuntu.com/mirrors.tuna.tsinghua.edu.cn/g' /etc/apt/sources.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# pip 换国内源
RUN pip3 config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --no-cache-dir pytest

WORKDIR /opt/biz-app

COPY agent-mapping.conf /opt/agent-sidecar/
COPY collector.sh /opt/agent-sidecar/
COPY entrypoint.sh /opt/agent-sidecar/
RUN chmod +x /opt/agent-sidecar/collector.sh /opt/agent-sidecar/entrypoint.sh

RUN mkdir -p /var/log/app

ENTRYPOINT ["bash", "/opt/agent-sidecar/entrypoint.sh"]