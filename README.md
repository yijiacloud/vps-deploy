# vps-deploy

一键在 VPS 上部署 **New API** + **Shadowsocks** 节点，并自动生成 Clash 可用的 YAML 配置文件。

## 功能

| 步骤 | 说明 |
|------|------|
| 1 | 交互输入服务器 IP、用户名、密码 |
| 2 | 自动安装 Docker（如未安装） |
| 3 | 自动部署 New API（端口 3000） |
| 4 | 自动部署 Shadowsocks AEAD 节点（端口 8388，chacha20-ietf-poly1305） |
| 5 | 自动配置 UFW 防火墙，放行 SSH / 3000 / 8388 |
| 6 | 自动生成本地 Clash YAML 配置文件 |

## 环境要求

- **本地**：Python 3.8+（Windows / macOS / Linux 均可）
- **服务器**：Ubuntu / Debian 系统，root 或 sudo 权限

## 快速开始

### Windows

双击 `deploy.bat`，或命令行运行：

```cmd
python deploy.py
```

### macOS / Linux

```bash
python3 deploy.py
```

按提示输入服务器 IP、用户名和密码即可。

## 命令行参数

```
python deploy.py --help
```

| 参数 | 说明 |
|------|------|
| `--host` | 服务器 IP 或域名（跳过交互输入） |
| `--username` | SSH 用户名（默认 root） |
| `--password` | SSH 密码（不填则交互输入） |
| `--ssh-port` | SSH 端口（默认 22） |
| `--output` | Clash YAML 输出路径（默认 clash-config.yaml） |
| `--regenerate-password` | 重新生成 Shadowsocks 密码（默认保留已有密码） |
| `--dry-run` | 预览模式，不连接服务器 |
| `--skip-newapi` | 跳过 New API 部署 |
| `--skip-shadowsocks` | 跳过 Shadowsocks 部署 |

## 使用示例

```bash
# 交互模式
python deploy.py

# 指定 IP，输出到自定义路径
python deploy.py --host 1.2.3.4 --output my-clash.yaml

# 只部署 Shadowsocks
python deploy.py --skip-newapi

# 预览不执行
python deploy.py --dry-run
```

## 部署后

- **New API**：浏览器访问 `http://<服务器IP>:3000`，首次进入设置管理员账号
- **Shadowsocks**：使用生成的 `clash-config.yaml` 导入 Clash / Clash Verge / ClashX 等客户端

## 幂等性

脚本支持重复运行：
- Docker 已安装则跳过安装
- Shadowsocks 已有配置则保留原密码
- 容器存在则重建，数据卷保留

## 技术细节

- Shadowsocks 服务端：[shadowsocks-rust](https://github.com/shadowsocks/shadowsocks-rust)
- New API：[calciumion/new-api](https://github.com/Calcium-Ion/new-api)
- 加密方式：`chacha20-ietf-poly1305`（AEAD）
- 网络模式：`host`（避免 Docker NAT 影响 UDP）
- 自动依赖安装：paramiko（SSH 库）

## License

MIT
