from django.urls import path
from django.urls.conf import include
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from admin_rest.restful_admin import IsStaffAccess
import seo.api_urls
import core.api_urls
import notifications.api_urls
import public_api.urls.coingecko
import public_api.urls.common
import public_api.urls.nomics

_STAFF = [IsStaffAccess]

v1 = []

v1.extend(core.api_urls.inouts.urlpatterns)
v1.extend(core.api_urls.orders.urlpatterns)
v1.extend(core.api_urls.stats.urlpatterns)
v1.extend(core.api_urls.wallets.urlpatterns)
v1.extend(core.api_urls.facade.urlpatterns)
v1.extend(notifications.api_urls.urlpatterns)
v1.extend(seo.api_urls.urlpatterns)

urlpatterns = [
    path('schema/', SpectacularAPIView.as_view(permission_classes=_STAFF), name='schema'),
    path('schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema', permission_classes=_STAFF), name='swagger-ui'),
    path('schema/redoc/', SpectacularRedocView.as_view(url_name='schema', permission_classes=_STAFF), name='redoc'),

    path('public/v1/', include(public_api.urls.common.urlpatterns)),
    path('external/nomics/', include(public_api.urls.nomics.urlpatterns)),
    path('external/coingecko/', include(public_api.urls.coingecko.urlpatterns)),
    path('v1/', include(v1)),
]
