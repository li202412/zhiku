import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from app.core.background_init import init_manager
from app.core.failed_response_register import register_exception_handlers
from app.core.logger_handler import logger
from app.core.settings import settings
from app.db.db_config import init_db, seed_test_user
from app.db.redis_config import close_redis, connect_redis
from app.graph.routers.graph_router import graph_router
from app.router.chat import chat_router
from app.router.health import health_router
from app.router.knowledge_router import knowledge_router
from app.router.user import file_router, user_router
from app.services.database_session_manager import init_database_session_manager

# 加载环境变量
load_dotenv()

app = FastAPI()

# 集成限流中间件（暂时注释掉，以免在调试阶段干扰正常请求）
# RateLimitMiddleware 基于令牌桶实现，每 60 秒允许 100 个请求
# 正式部署时可根据接口负载调整限流策略
# 所有限流（包括路由上的 Depends(rate_limit(...))）通过 RATE_LIMIT_ENABLED=false 一键关闭
# app.add_middleware(RateLimitMiddleware, limit=100, window=60)

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(round(process_time, 4))
    return response

# 集成API路由
app.include_router(chat_router)
app.include_router(knowledge_router)
app.include_router(health_router)
app.include_router(user_router)
app.include_router(file_router)
app.include_router(graph_router)




app.add_middleware(
    CORSMiddleware,
    # 来源白名单由 CORS_ORIGINS 控制（逗号分隔）；配成 "*" 时浏览器会因 credentials 组合失效，
    # 因此默认只放行本地前端，生产部署时按实际域名追加
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载媒体文件目录（头像等上传文件）
media_dir = os.path.join(os.path.dirname(__file__), "media")
os.makedirs(media_dir, exist_ok=True)
app.mount("/media", StaticFiles(directory=media_dir), name="media")

# 注册异常处理函数
register_exception_handlers(app)

@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}

@app.get('/health')
async def health_check():
    return {"message": "The service is healthy"}


@app.on_event("startup")
async def startup_event():
    """应用启动时初始化会话管理器"""
    # 初始化数据库表结构（自动创建/迁移）
    await init_db()
    logger.info("数据库表结构初始化完成")

    # 确保测试用户存在
    await seed_test_user()

    # 使用数据库版本的会话管理器
    await init_database_session_manager()
    logger.info("数据库会话管理器初始化完成")

    # 连接Redis
    await connect_redis()
    logger.info("Redis连接初始化完成")

    # 演示账号与生产环境冲突时告警（公网部署务必关闭）
    if settings.SEED_DEMO_USER and settings.ENV.lower() == "prod":
        logger.warning("⚠️ ENV=prod 但 SEED_DEMO_USER 仍为 true：请关闭演示账号并修改默认口令")

    # 安全提示：SECRET_KEY 为空/过弱时 JWT 可被伪造（不阻塞启动，便于本地快速体验）
    secret = settings.SECRET_KEY or ""
    if len(secret) < 16 or secret.startswith("MY_JWT_SECRET") or secret.startswith("please-change"):
        logger.warning("⚠️ SECRET_KEY 未设置或过弱：JWT 可被伪造，请在 backend/.env 配置随机长字符串")

    # 检查并重排序模型（在后台异步加载）
    await init_manager.start()
    logger.info("部分资源正在初始化")

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理资源"""
    # 关闭 Redis 连接
    await close_redis()
    logger.info("Redis连接已关闭")

    # 停止图谱构建 worker 并关闭 Neo4j 驱动（未配置时为空操作）
    await init_manager.stop_graph_worker()
    from app.graph.storage.neo4j_client import close_neo4j_driver
    await close_neo4j_driver()
    logger.info("Neo4j驱动已关闭")

    # 关闭 SQLAlchemy 引擎（释放 aiomysql 连接池，避免 GC 时事件循环已关闭）
    from app.db.db_config import async_engine
    await async_engine.dispose()
    logger.info("数据库引擎已关闭")
