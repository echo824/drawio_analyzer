# 油井压裂评价微服务 · 生产镜像
# 本地部署：docker compose up -d --build（推荐）
#           或 docker build -t oil-frac-service . && docker run -d -p 8000:8000 -v oilfrac_templates:/srv/app/templates oil-frac-service
FROM python:3.12-slim

WORKDIR /srv/app

# 可选构建参数：国内网络覆盖为镜像源（见 docker-compose.yml build.args）
ARG PIP_INDEX_URL=https://pypi.org/simple

# 运行依赖先行（利用层缓存）；gunicorn 仅生产镜像引入，不进 requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt gunicorn==23.0.*

# 应用代码；templates/ 是运行时数据（启动 fail-fast 巡检、热部署落盘都依赖它）
COPY app.py ./
COPY config_loader/ ./config_loader/
COPY core/ ./core/
COPY templates/ ./templates/

EXPOSE 8000

# 生产入口：gunicorn 直接 import 工厂产物 app:app，绕开 __main__（永不携带 debug 调试器）
# --capture-output：让应用 logger（模板巡检/校验告警日志）进容器 stdout，docker logs 可见
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", \
     "--access-logfile", "-", "--error-logfile", "-", "--capture-output", "app:app"]
