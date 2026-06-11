from django.urls import path

from routes import views

urlpatterns = [
    path('api/route/', views.route_view, name='route'),
    path('map/', views.map_view, name='map'),
]
