# Docker 双容器测试环境命令参考

```bash
cd "D:\U 盘\SpiderClaw"

# ── 查看运行状态 ──
docker ps

# ── 首次构建 ──
docker compose up -d --build                        # 构建两个镜像并后台启动

# ── 日常启动/关闭 ──
docker compose up -d                                # 启动（不重建）
docker compose down                                 # 停止并删除容器
docker compose restart                              # 重启所有服务
docker compose restart spiderclaw                 # 只重启 spiderclaw
docker compose restart biz-server                   # 只重启 biz-server

# ── 查看日志 ──
docker logs -f spiderclaw-agent                     # spiderclaw 实时日志（干净，无 TUI）
docker logs -f biz-server                           # biz-server 实时日志
docker logs --tail 50 spiderclaw-agent              # 最近 50 行

# ── 仪表盘（TUI）──
docker exec -it spiderclaw-agent spiderclaw         # 新终端进入仪表盘

docker exec -it -e TERM=xterm-256color spiderclaw-agent spiderclaw   #防止闪屏的指令，运行时加上终端环境变量，临时测试用（不建议运行在poweshell中，在vscode的终端不会闪屏）
# 退出仪表盘：按 Ctrl+c 或 Ctrl+q

  # ── 手动触发测试，全部测试文件 ──
  docker exec -it biz-server bash -c "cd /opt/biz-app && /opt/agent-sidecar/collector.sh exec python3 -m pytest app/tests/ -v --tb=long"

  # ── 测试 test_user_service.py 文件 ──
  docker exec -it biz-server bash -c "cd /opt/biz-app && /opt/agent-sidecar/collector.sh exec python3 -m pytest app/tests/test_user_service.py -v --tb=long"

  # ── 测试 test_calculator.py 文件 ──
  docker exec -it biz-server bash -c "cd /opt/biz-app && /opt/agent-sidecar/collector.sh exec python3 -m pytest app/tests/test_calculator.py -v --tb=long"


# ── 进入容器调试 ──
docker exec -it spiderclaw-agent bash               # 进 spiderclaw 容器
docker exec -it biz-server bash                     # 进 biz-server 容器

# ── 改了代码后 ──
docker compose restart                              # 改了 src/ 或 app/ 代码

# ── 改了依赖后 ──
docker compose up -d --build                        # 改了 requirements.txt 或 pyproject.toml

# ── 只重建单个服务 ──
docker compose up -d --build spiderclaw             # 只重建 spiderclaw
docker compose up -d --build biz-server             # 只重建 biz-server

# ── 彻底重来 ──
docker compose down -v                              # 停止 + 删除容器和数据卷
docker compose up -d --build                        # 重新构建
```

## 说明

- spiderclaw 容器默认以 `--no-dashboard` 模式运行，`docker logs` 输出干净
- 仪表盘需另开终端通过 `docker exec -it spiderclaw-agent spiderclaw` 查看
- `src/` 和 `app/` 代码改动只需 `restart`，无需重建镜像
- 依赖变更（requirements.txt / pyproject.toml）需要 `--build` 重建
