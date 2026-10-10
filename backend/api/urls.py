from django.urls import path
from rest_framework.routers import SimpleRouter

from . import hook, recordings
from .views import DeviceListView, GrantViewSet, MaterielView, MeView

router = SimpleRouter(trailing_slash=True)
router.register('grants', GrantViewSet, basename='grant')

urlpatterns = [
    path('me/', MeView.as_view()),
    path('devices/', DeviceListView.as_view()),
    path('media-auth/', hook.media_auth),
    path('admin/materiel/', MaterielView.as_view()),
    path('recordings/', recordings.RecordingListView.as_view()),
    path('recordings/ticket/', recordings.RecordingTicketView.as_view()),
    path('recordings/stream/', recordings.recording_stream),
    *router.urls,
]
