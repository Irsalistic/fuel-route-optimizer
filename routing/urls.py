from django.urls import path

from routing import views

urlpatterns = [
    path("api/route/", views.RouteView.as_view(), name="route"),
    path("map/", views.map_page, name="map"),
    path("", views.map_page, name="home"),
]
