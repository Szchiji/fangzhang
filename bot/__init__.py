from aiogram import Dispatcher
from .start import router as start_router
from .group import router as group_router
from .subscription import router as sub_router
from .certified import router as cert_router
from .checkin import router as checkin_router
from .nearby import router as nearby_router
from .rating import router as rating_router
from .coupon import router as coupon_router
from .tasks import router as tasks_router
from .admin import router as admin_router


def register_all(dp: Dispatcher):
    for r in [start_router, group_router, sub_router, cert_router,
              checkin_router, nearby_router, rating_router, coupon_router,
              tasks_router, admin_router]:
        dp.include_router(r)
