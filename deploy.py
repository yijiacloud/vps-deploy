#!/usr/bin/env python3
"""
VPS 一键部署脚本 —— New API + Shadowsocks (Clash 兼容)
=======================================================
功能：
  1. 交互输入服务器 IP、用户名、密码
  2. 自动安装 Docker
  3. 自动部署 New API (端口 3000)
  4. 自动部署 Shadowsocks AEAD 节点 (端口 8388)
  5. 自动配置 UFW 防火墙
  6. 自动生成本地 Clash YAML 配置文件

用法：
  python deploy.py                    # 交互模式
  python deploy.py --host 1.2.3.4     # 指定 IP
  python deploy.py --dry-run          # 预览不执行
  python deploy.py --regenerate-password  # 重新生成 SS 密码
"""

from __future__ import annotations

import argparse
import getpass
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# ─── 常量 ───────────────────────────────────────────────
NEWAPI_DIR = "/opt/new-api"
NEWAPI_COMPOSE = f"{NEWAPI_DIR}/docker-compose.yml"
NEWAPI_CONTAINER = "new-api"
NEWAPI_PORT = 3000
NEWAPI_IMAGE = "calciumion/new-api:v1.0.0-rc.26"

SS_DIR = "/opt/shadowsocks"
SS_CONFIG = f"{SS_DIR}/config.json"
SS_COMPOSE = f"{SS_DIR}/docker-compose.yml"
SS_CONTAINER = "shadowsocks"
SS_PORT = 8388
SS_CIPHER = "chacha20-ietf-poly1305"
SS_IMAGE = "ghcr.io/shadowsocks/ssserver-rust:latest"

DEFAULT_OUTPUT = "clash-config.yaml"


# ─── 依赖检查 ────────────────────────────────────────────
def ensure_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        print("正在安装 paramiko ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
        import paramiko
        return paramiko


# ─── 参数解析 ────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="一键部署 New API + Shadowsocks 到 VPS，并生成 Clash 配置"
    )
    p.add_argument("--host", help="服务器 IP 或域名")
    p.add_argument("--username", default="root", help="SSH 用户名 (默认 root)")
    p.add_argument("--password", help="SSH 密码 (不填则交互输入)")
    p.add_argument("--ssh-port", type=int, default=22, help="SSH 端口 (默认 22)")
    p.add_argument("--output", default=DEFAULT_OUTPUT, help="本地 Clash YAML 输出路径")
    p.add_argument("--regenerate-password", action="store_true", help="重新生成 SS 密码")
    p.add_argument("--dry-run", action="store_true", help="预览模式，不连接服务器")
    p.add_argument("--skip-newapi", action="store_true", help="跳过 New API 部署")
    p.add_argument("--skip-shadowsocks", action="store_true", help="跳过 Shadowsocks 部署")
    return p.parse_args()


def prompt_credentials(args):
    host = args.host or input("服务器 IP 或域名: ").strip()
    if not host:
        raise ValueError("服务器地址不能为空")
    username = args.username or input("SSH 用户名 [root]: ").strip() or "root"
    password = args.password or getpass.getpass("SSH 密码: ")
    if not password:
        raise ValueError("SSH 密码不能为空")
    return host, username, password


# ─── SSH 工具函数 ────────────────────────────────────────
def run_remote(client, cmd: str, password: str, use_sudo: bool, check: bool = True, timeout: int = 120):
    if use_sudo:
        cmd = f"sudo -S -p '' {cmd}"
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=False, timeout=timeout)
    if use_sudo:
        stdin.write(password + "\n")
        stdin.flush()
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    if check and exit_code != 0:
        raise RuntimeError(f"远程命令失败 (exit {exit_code}): {cmd}\n{err or out}")
    return out, err, exit_code


def upload_text(client, text: str, remote_path: str):
    sftp = client.open_sftp()
    try:
        with sftp.file(remote_path, "w") as f:
            f.write(text)
    finally:
        sftp.close()


def download_text(client, remote_path: str) -> str:
    sftp = client.open_sftp()
    try:
        with sftp.file(remote_path, "r") as f:
            return f.read().decode("utf-8")
    finally:
        sftp.close()


def remote_exists(client, path: str, password: str, use_sudo: bool) -> bool:
    out, _, _ = run_remote(client, f"test -f {path} && echo yes || echo no", password, use_sudo, check=False)
    return "yes" in out


# ─── Docker 安装 ─────────────────────────────────────────
def install_docker(client, password: str, use_sudo: bool):
    out, _, _ = run_remote(client, "docker --version 2>/dev/null", password, use_sudo, check=False)
    if out:
        print(f"Docker 已安装: {out}")
        return
    print("安装 Docker ...")
    run_remote(client, "apt-get update -y", password, use_sudo, timeout=180)
    run_remote(client, "apt-get install -y docker.io docker-compose-v2", password, use_sudo, timeout=180)
    run_remote(client, "systemctl enable docker && systemctl start docker", password, use_sudo)
    out, _, _ = run_remote(client, "docker --version", password, use_sudo)
    print(f"Docker 安装完成: {out}")


# ─── 防火墙配置 ──────────────────────────────────────────
def configure_firewall(client, password: str, use_sudo: bool, ssh_port: int):
    out, _, _ = run_remote(client, "which ufw 2>/dev/null", password, use_sudo, check=False)
    if not out:
        run_remote(client, "apt-get install -y ufw", password, use_sudo, timeout=120)
    for rule in [f"{ssh_port}/tcp", f"{NEWAPI_PORT}/tcp", f"{SS_PORT}/tcp", f"{SS_PORT}/udp"]:
        run_remote(client, f"ufw allow {rule}", password, use_sudo, check=False)
    run_remote(client, "echo 'y' | ufw enable", password, use_sudo, check=False)
    status, _, _ = run_remote(client, "ufw status", password, use_sudo, check=False)
    print(f"防火墙状态:\n{status}")


# ─── New API 部署 ────────────────────────────────────────
def build_newapi_compose() -> str:
    session_secret = secrets.token_hex(24)
    return f"""services:
  new-api:
    image: {NEWAPI_IMAGE}
    container_name: {NEWAPI_CONTAINER}
    restart: always
    command: --log-dir /app/logs
    ports:
      - "{NEWAPI_PORT}:{NEWAPI_PORT}"
    volumes:
      - ./data:/data
      - ./logs:/app/logs
    environment:
      TZ: Asia/Shanghai
      SESSION_SECRET: {session_secret}
      CRYPTO_SECRET: {session_secret}
      ERROR_LOG_ENABLED: "true"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:{NEWAPI_PORT}/api/status | grep -q 'success' || exit 1"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
"""


def deploy_newapi(client, password: str, use_sudo: bool):
    print("\n=== 部署 New API ===")
    run_remote(client, f"mkdir -p {NEWAPI_DIR}/data {NEWAPI_DIR}/logs", password, use_sudo)

    compose_text = build_newapi_compose()
    tmp = f"{NEWAPI_DIR}/docker-compose.yml.tmp"
    upload_text(client, compose_text, tmp)
    run_remote(client, f"mv {tmp} {NEWAPI_COMPOSE}", password, use_sudo)

    out, _, _ = run_remote(client, "docker compose version 2>/dev/null", password, use_sudo, check=False)
    if out:
        print("使用 Docker Compose 启动 New API ...")
        run_remote(client, f"cd {NEWAPI_DIR} && docker compose up -d", password, use_sudo, timeout=180)
    else:
        print("使用 docker run 启动 New API ...")
        run_remote(client, f"docker rm -f {NEWAPI_CONTAINER} 2>/dev/null || true", password, use_sudo, check=False)
        run_remote(client, (
            f"docker run -d --name {NEWAPI_CONTAINER} --restart always "
            f"-p {NEWAPI_PORT}:{NEWAPI_PORT} "
            f"-v {NEWAPI_DIR}/data:/data -v {NEWAPI_DIR}/logs:/app/logs "
            f"-e TZ=Asia/Shanghai -e ERROR_LOG_ENABLED=true "
            f"{NEWAPI_IMAGE} --log-dir /app/logs"
        ), password, use_sudo, timeout=180)

    print("等待 New API 启动 ...")
    for i in range(30):
        time.sleep(3)
        status, _, _ = run_remote(
            client,
            f"docker inspect --format '{{{{.State.Health.Status}}}}' {NEWAPI_CONTAINER} 2>/dev/null || echo starting",
            password, use_sudo, check=False,
        )
        if "healthy" in status:
            print("New API 健康检查通过")
            break
        if i == 29:
            print(f"警告: New API 健康检查超时 (状态: {status})，请手动检查")

    out, _, _ = run_remote(client, f"curl -s http://127.0.0.1:{NEWAPI_PORT}/api/status", password, use_sudo, check=False)
    print(f"New API 状态: {out[:120]}")
    print(f"New API 访问地址: http://<服务器IP>:{NEWAPI_PORT}")


# ─── Shadowsocks 部署 ────────────────────────────────────
def generate_ss_password() -> str:
    return secrets.token_urlsafe(24)


def build_ss_config(password: str) -> str:
    config = {
        "server": "0.0.0.0",
        "server_port": SS_PORT,
        "password": password,
        "method": SS_CIPHER,
        "mode": "tcp_and_udp",
        "timeout": 300,
    }
    return json.dumps(config, indent=2) + "\n"


def build_ss_compose() -> str:
    return f"""services:
  shadowsocks:
    image: {SS_IMAGE}
    container_name: {SS_CONTAINER}
    restart: always
    network_mode: host
    volumes:
      - ./config.json:/etc/shadowsocks-rust/config.json:ro
"""


def deploy_shadowsocks(client, password: str, use_sudo: bool, regenerate: bool):
    print("\n=== 部署 Shadowsocks ===")
    run_remote(client, f"mkdir -p {SS_DIR}", password, use_sudo)

    existing_password = None
    if not regenerate and remote_exists(client, SS_CONFIG, password, use_sudo):
        try:
            text = download_text(client, SS_CONFIG)
            existing_password = json.loads(text).get("password")
            print("检测到已有 Shadowsocks 配置，保留原密码")
        except Exception:
            print("已有配置无法解析，将生成新密码")

    final_password = existing_password or generate_ss_password()
    config_text = build_ss_config(final_password)
    compose_text = build_ss_compose()

    tmp_cfg = f"{SS_DIR}/config.json.tmp"
    tmp_cps = f"{SS_DIR}/docker-compose.yml.tmp"
    upload_text(client, config_text, tmp_cfg)
    upload_text(client, compose_text, tmp_cps)
    run_remote(client, f"mv {tmp_cfg} {SS_CONFIG}", password, use_sudo)
    run_remote(client, f"mv {tmp_cps} {SS_COMPOSE}", password, use_sudo)

    out, _, _ = run_remote(client, "docker compose version 2>/dev/null", password, use_sudo, check=False)
    if out:
        print("使用 Docker Compose 启动 Shadowsocks ...")
        run_remote(client, f"cd {SS_DIR} && docker compose up -d", password, use_sudo, timeout=120)
    else:
        print("使用 docker run 启动 Shadowsocks ...")
        run_remote(client, f"docker rm -f {SS_CONTAINER} 2>/dev/null || true", password, use_sudo, check=False)
        run_remote(client, (
            f"docker run -d --name {SS_CONTAINER} --restart always "
            f"--network host "
            f"-v {SS_CONFIG}:/etc/shadowsocks-rust/config.json:ro "
            f"{SS_IMAGE}"
        ), password, use_sudo, timeout=120)

    status, _, _ = run_remote(
        client,
        f"docker inspect --format '{{{{.State.Status}}}}' {SS_CONTAINER}",
        password, use_sudo,
    )
    if status != "running":
        raise RuntimeError(f"Shadowsocks 容器状态异常: {status}")
    print(f"Shadowsocks 容器状态: {status}")

    listeners, _, _ = run_remote(client, f"ss -ltnup | grep ':{SS_PORT}'", password, use_sudo, check=False)
    if listeners:
        print(f"端口 {SS_PORT} TCP/UDP 监听正常")
    else:
        print(f"警告: 端口 {SS_PORT} 未检测到监听")

    return final_password


# ─── Clash YAML 生成 ─────────────────────────────────────
def build_clash_yaml(host: str, ss_password: str) -> str:
    quoted = json.dumps(ss_password)
    return f"""mixed-port: 7890
allow-lan: false
mode: rule
log-level: info

proxies:
  - name: "{host} Shadowsocks"
    type: ss
    server: {host}
    port: {SS_PORT}
    cipher: {SS_CIPHER}
    password: {quoted}
    udp: true

proxy-groups:
  - name: "Proxy"
    type: select
    proxies:
      - "{host} Shadowsocks"
      - DIRECT
  - name: "OpenAI"
    type: select
    proxies:
      - DIRECT
      - Proxy

rules:
  - DOMAIN-SUFFIX,openai.com,OpenAI
  - DOMAIN-SUFFIX,chatgpt.com,OpenAI
  - DOMAIN-SUFFIX,oaistatic.com,OpenAI
  - DOMAIN-SUFFIX,oaiusercontent.com,OpenAI
  - DOMAIN,challenges.cloudflare.com,OpenAI
  - DOMAIN,cdn.auth0.com,OpenAI
  - MATCH,Proxy
"""


# ─── 主流程 ──────────────────────────────────────────────
def main():
    args = parse_args()

    if args.dry_run:
        pwd = generate_ss_password()
        print("=== Dry Run 预览 ===")
        print(f"1. 连接 {args.username}@{args.host or '<交互输入>'}:{args.ssh_port}")
        print(f"2. 安装 Docker (apt-get)")
        print(f"3. 部署 New API -> 端口 {NEWAPI_PORT}")
        print(f"4. 部署 Shadowsocks -> 端口 {SS_PORT}")
        print(f"5. 防火墙放行: {args.ssh_port}/tcp, {NEWAPI_PORT}/tcp, {SS_PORT}/tcp, {SS_PORT}/udp")
        print(f"6. 生成 Clash YAML -> {args.output}")
        print("\n=== Clash YAML 示例 ===")
        print(build_clash_yaml(args.host or "SERVER_IP", pwd), end="")
        return

    paramiko = ensure_paramiko()
    host, username, password = prompt_credentials(args)

    print(f"\n连接 {username}@{host}:{args.ssh_port} ...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host, port=args.ssh_port, username=username, password=password,
        timeout=20, banner_timeout=20, auth_timeout=20,
    )

    try:
        uid_out, _, _ = run_remote(client, "id -u", password, False)
        use_sudo = uid_out.strip() != "0"
        if use_sudo:
            print("非 root 用户，使用 sudo")

        install_docker(client, password, use_sudo)
        configure_firewall(client, password, use_sudo, args.ssh_port)

        if not args.skip_newapi:
            deploy_newapi(client, password, use_sudo)

        ss_password = None
        if not args.skip_shadowsocks:
            ss_password = deploy_shadowsocks(client, password, use_sudo, args.regenerate_password)

        if ss_password:
            output = Path(args.output).expanduser().resolve()
            output.write_text(build_clash_yaml(host, ss_password), encoding="utf-8")
            print(f"\nClash 配置已生成: {output}")
        print("\n=== 部署完成 ===")
        print(f"  New API:    http://{host}:{NEWAPI_PORT}")
        print(f"  Shadowsocks: {host}:{SS_PORT} ({SS_CIPHER})")
        if ss_password:
            print(f"  Clash YAML:  {args.output}")
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消")
        raise SystemExit(130)
