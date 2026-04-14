from django.urls import path
from . import views

urlpatterns = [
    # ── Public pages
    path('',                              views.home,                  name='home'),
    path('plan/',                         views.plan_trip,             name='plan_trip'),
    path('chat/',                         views.chat_with_ai,          name='chat'),

    # ── Auth
    path('login/',                        views.login_view,            name='login'),
    path('logout/',                       views.logout_view,           name='logout'),
    path('register/',                     views.register_view,         name='register'),
    path('forgot-password/',              views.forgot_password_view,  name='forgot_password'),
    path('reset-password/<uidb64>/<token>/', views.reset_password_view, name='reset_password'),

    # ── Logged-in: generate pending trip after login
    path('generate-pending/',             views.generate_pending_trip, name='generate_pending_trip'),

    # ── Dashboard & profile
    path('dashboard/',                    views.dashboard_view,        name='dashboard'),
    path('profile/',                      views.profile_view,          name='profile'),

    # ── Trip actions
    path('trip/<int:trip_id>/',           views.view_trip,             name='view_trip'),
   
    path('trip/<int:trip_id>/delete/',    views.delete_trip,           name='delete_trip'),
    path('trip/<int:pk>/download/',             views.download_trip_pdf,      name='download_trip_pdf'),
]