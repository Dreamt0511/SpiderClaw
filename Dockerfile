FROM python:3.12-slim

# 设置时区为中国时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# pip 换国内源（加速下载）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    # 仅保留必要工具，大幅缩小镜像体积
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean \
    && rm -rf /var/cache/apt/*

# 复制本地已下载的lark-cli二进制包（无需在线下载，src目录不会被dockerignore忽略）
COPY src/lark-cli-linux-amd64.tar.gz /tmp/lark-cli.tar.gz

# 安装 lark-cli 飞书命令行工具
RUN mkdir -p /tmp/lark-cli && \
    tar -xzf /tmp/lark-cli.tar.gz -C /tmp/lark-cli && \
    mv /tmp/lark-cli/lark-cli /usr/local/bin/lark && \
    chmod +x /usr/local/bin/lark && \
    # 创建别名，同时支持 lark 和 lark-cli 两种调用方式
    ln -sf /usr/local/bin/lark /usr/local/bin/lark-cli && \
    rm -rf /tmp/lark-cli /tmp/lark-cli.tar.gz && \
    # 验证安装（两种命令都测试）
    lark --version && \
    lark-cli --version

WORKDIR /app

# 复制依赖文件
COPY requirements.txt pyproject.toml ./

# 安装Python依赖
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt && pip install -e .

# 复制启动脚本
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# 复制默认配置文件（作为兜底）
COPY src/config/agent-config.example.yaml /app/src/config/agent-config.yaml.default
COPY src/config/services.docker.yaml /app/src/config/services.yaml.default

ENTRYPOINT ["bash", "entrypoint.sh"]
CMD ["spiderclaw", "--no-dashboard"]