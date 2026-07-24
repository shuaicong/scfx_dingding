#!/usr/bin/env bash
# =============================================================================
# scfx_dingding 部署脚本
# 适用于云服务器 Docker 环境。支持首次部署、更新、日志查看等操作。
#
# 用法:
#   ./deploy.sh install   # 首次部署（构建镜像并启动）
#   ./deploy.sh update    # 更新代码并重启
#   ./deploy.sh start     # 启动服务
#   ./deploy.sh stop      # 停止服务
#   ./deploy.sh restart   # 重启服务
#   ./deploy.sh status    # 查看运行状态
#   ./deploy.sh logs      # 查看实时日志
#   ./deploy.sh once      # 手动执行一次同步（不修改后台服务）
# =============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.yml"
ENV_FILE="${PROJECT_DIR}/.env"
SERVICE_NAME="scfx-dingding"

cd "$PROJECT_DIR"

# ========== 颜色输出 ==========
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ========== 前置检查 ==========
check_prerequisites() {
    if ! command -v docker &>/dev/null; then
        error "Docker 未安装。请先安装 Docker："
        error "  curl -fsSL https://get.docker.com | sh"
        exit 1
    fi

    if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
        error "docker-compose 未安装。"
        exit 1
    fi

    if [ ! -f "$ENV_FILE" ]; then
        warn ".env 文件不存在，从 .env.example 创建..."
        cp .env.example "$ENV_FILE"
        info "已创建 .env 文件，请编辑并填入你的配置："
        info "  vi $ENV_FILE"
        echo ""
        echo "  重要配置项："
        echo "    DINGTALK_APP_KEY       - 钉钉应用 Key"
        echo "    DINGTALK_APP_SECRET    - 钉钉应用 Secret"
        echo "    DINGTALK_ROBOT_WEBHOOK - 机器人通知 Webhook（可选）"
        echo ""
        read -r -p "编辑完成后，输入 yes 继续部署: " confirm
        if [ "$confirm" != "yes" ]; then
            info "部署已取消"
            exit 0
        fi
    fi
}

dc() {
    if command -v docker-compose &>/dev/null; then
        docker-compose "$@"
    else
        docker compose "$@"
    fi
}

# ========== 命令实现 ==========

cmd_install() {
    info "===== scfx_dingding 首次部署 ====="
    check_prerequisites

    info "创建数据卷..."
    docker volume create scfx_dingding_data 2>/dev/null || true

    info "构建镜像并启动服务..."
    dc -f "$COMPOSE_FILE" up -d --build

    info "查看运行状态..."
    dc -f "$COMPOSE_FILE" ps

    echo ""
    info "部署完成！"
    info "  查看日志:  ./deploy.sh logs"
    info "  执行一次同步测试: ./deploy.sh once"
}

cmd_update() {
    info "===== 更新 scfx_dingding ====="

    if [ ! -f "$ENV_FILE" ]; then
        error ".env 文件不存在！请先运行 ./deploy.sh install"
        exit 1
    fi

    info "拉取最新代码（如果是 git 仓库）..."
    if git status &>/dev/null; then
        git pull
    else
        warn "非 git 仓库，跳过代码更新"
    fi

    info "重新构建并启动..."
    dc -f "$COMPOSE_FILE" up -d --build

    info "更新完成"
}

cmd_start() {
    info "启动服务..."
    dc -f "$COMPOSE_FILE" start
    info "服务已启动"
}

cmd_stop() {
    info "停止服务..."
    dc -f "$COMPOSE_FILE" stop
    info "服务已停止"
}

cmd_restart() {
    info "重启服务..."
    dc -f "$COMPOSE_FILE" restart
    info "服务已重启"
}

cmd_status() {
    echo "===== 服务状态 ====="
    dc -f "$COMPOSE_FILE" ps

    echo ""
    echo "===== 容器资源占用 ====="
    docker stats --no-stream "$SERVICE_NAME" 2>/dev/null || echo "容器未运行"

    echo ""
    echo "===== 最近日志（10行） ====="
    dc -f "$COMPOSE_FILE" logs --tail=10 "$SERVICE_NAME" 2>/dev/null || true
}

cmd_logs() {
    dc -f "$COMPOSE_FILE" logs -f "$SERVICE_NAME"
}

cmd_once() {
    info "执行一次同步任务..."
    if [ ! -f "$ENV_FILE" ]; then
        error ".env 文件不存在！请先运行 ./deploy.sh install"
        exit 1
    fi

    # 检查容器是否运行
    if docker ps --format '{{.Names}}' | grep -q "^${SERVICE_NAME}$"; then
        info "容器正在运行，在容器中执行同步..."
        dc exec -T "$SERVICE_NAME" python main.py
    else
        info "容器未运行，启动临时容器执行同步..."
        dc -f "$COMPOSE_FILE" run --rm "$SERVICE_NAME" python main.py
    fi
}

# ========== 主入口 ==========
case "${1:-help}" in
    install)
        cmd_install
        ;;
    update)
        cmd_update
        ;;
    start)
        cmd_start
        ;;
    stop)
        cmd_stop
        ;;
    restart)
        cmd_restart
        ;;
    status)
        cmd_status
        ;;
    logs)
        cmd_logs
        ;;
    once)
        cmd_once
        ;;
    help)
        echo "用法: ./deploy.sh <command>"
        echo ""
        echo "命令:"
        echo "  install   首次部署（构建镜像并启动）"
        echo "  update    更新代码并重启"
        echo "  start     启动服务"
        echo "  stop      停止服务"
        echo "  restart   重启服务"
        echo "  status    查看运行状态"
        echo "  logs      查看实时日志"
        echo "  once      手动执行一次同步"
        ;;
    *)
        error "未知命令: $1"
        echo "用法: ./deploy.sh {install|update|start|stop|restart|status|logs|once}"
        exit 1
        ;;
esac
