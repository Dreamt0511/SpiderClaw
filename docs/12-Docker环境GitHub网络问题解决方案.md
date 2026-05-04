# Docker环境GitHub网络问题解决方案

## 问题现象
Docker容器内无法访问GitHub，表现为：
- `git clone` 超时或连接拒绝
- `curl https://github.com` 连接失败
- 域名解析到`127.0.0.1`导致无法访问

## 原因分析
Watt Toolkit（瓦特工具箱）的DNS劫持功能会将`github.com`解析到本地`127.0.0.1`，通过本地代理实现加速。但在Docker容器环境下：
1. 容器内的`127.0.0.1`指向容器自身，而非宿主机
2. 即使配置代理指向宿主机IP，也会因为Watt Toolkit的监听限制或防火墙规则导致连接失败
3. host网络模式在Windows Docker Desktop下存在兼容性问题，无法直接复用宿主机网络栈

## 最终解决方案
采用**桥接网络+官方IP硬编码**方案，完全绕过DNS劫持：

### 1. 修改`docker-compose.yml`配置
在`spiderclaw`服务下添加`extra_hosts`配置，直接绑定GitHub官方IP：
```yaml
services:
  spiderclaw:
    # ... 其他配置 ...
    extra_hosts:
      - "github.com:20.205.243.166"  # GitHub官方IP，绕过本地DNS劫持
    networks:
      - spiderclaw-net  # 使用桥接网络

# 定义桥接网络
networks:
  spiderclaw-net:
    driver: bridge
```

### 2. 移除不必要的配置
- 删除`network_mode: host`配置
- 删除容器内的Git代理配置（`entrypoint.sh`中不需要再配置`http.proxy`）
- 删除Docker Desktop全局代理配置

### 3. 重启容器生效
```bash
docker compose up -d --force-recreate spiderclaw
```

## 验证方法
### 1. 基础连通性测试
```bash
# 测试HTTPS访问
docker compose exec spiderclaw curl -I https://github.com
```
✅ 正常结果：返回 `HTTP/2 200`

### 2. Git克隆测试
```bash
# 测试Git仓库克隆
docker compose exec spiderclaw git clone --depth 1 https://github.com/Dreamt0511/AutoFix_Test_rep /tmp/test_repo
```
✅ 正常结果：仓库成功克隆到容器内

### 3. 功能验证
触发一次自动修复流程，确认系统能够正常：
- 拉取目标仓库代码
- 提交代码修改
- 创建Pull Request

## IP更新方法
如果后续GitHub官方IP发生变更导致访问失败：
1. 访问 https://github.com.ipaddress.com/ 查询最新的GitHub公网IP
2. 更新`docker-compose.yml`中`extra_hosts`字段的IP地址
3. 执行 `docker compose up -d --force-recreate spiderclaw` 重启容器

## 方案优势
1. 配置简单，无需修改代理设置或Watt Toolkit配置
2. 稳定性高，不依赖本地代理服务的可用性
3. 性能好，直接访问GitHub官方节点，没有代理中转开销
4. 兼容性强，适用于所有Docker环境（Windows/Mac/Linux）