from django.urls import path, include
from . import views
from .api import (
    api_login, api_logout,
    api_students_list, api_create_student, api_student_detail,
    api_update_student, api_delete_student,
    api_reports_list, api_create_report,
    api_attendance_list, api_mark_attendance,
    api_timetable_list, api_create_timetable_entry,
    api_leaderboard, api_rewards_list, api_student_points,
    api_documents_list, api_upload_document,
    api_analytics_overview, api_category_distribution, api_trend_analysis,
    api_parent_children, api_parent_notifications,
    api_dashboard_data, api_health,
)

urlpatterns = [
    # DEBUG & TEST VIEWS
    path('test-streams/', views.test_streams, name='test_streams'),
    path('debug-streams/', views.debug_streams, name='debug_streams'),
    
    # SCHOOL SETUP
    path('school-setup/', views.school_setup, name='school_setup'),
    path('bulk-upload-students/', views.bulk_upload_students, name='bulk_upload_students'),
    
    # AUTHENTICATION
    path('login/', views.custom_login, name='login'),
    path('logout/', views.custom_logout, name='logout'),
    path('register/', views.register, name='register'),
    
    # PASSWORD RESET
    path('request-reset/', views.request_password_reset, name='request_password_reset'),
    path('admin-reset-requests/', views.admin_reset_requests, name='admin_reset_requests'),
    path('approve-reset/<int:reset_id>/', views.approve_reset, name='approve_reset'),
    path('reject-reset/<int:reset_id>/', views.reject_reset, name='reject_reset'),
    
    # DASHBOARDS
    path('dashboard/', views.dashboard_redirect, name='dashboard'),
    path('choose-stream/', views.choose_stream, name='choose_stream'),
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('class-teacher-dashboard/', views.class_teacher_dashboard, name='class_teacher_dashboard'),
    path('teacher-dashboard/', views.teacher_dashboard, name='teacher_dashboard'),
    path('analytics-dashboard/', views.analytics_dashboard, name='analytics_dashboard'),
    path('aether-dashboard/', views.aether_dashboard, name='aether_dashboard'),
    path('gamification-dashboard/', views.gamification_dashboard, name='gamification_dashboard'),
    path('timetable/', views.timetable_view, name='timetable'),
    path('timetable/create/', views.create_timetable, name='create_timetable'),
    path('attendance/', views.attendance_list, name='attendance_list'),
    path('mobile-dashboard/', views.mobile_dashboard, name='mobile_dashboard'),
    
    # STUDENT MANAGEMENT
    path('student/<int:student_id>/', views.student_profile, name='student_profile'),
    path('student/<int:student_id>/edit/', views.edit_student, name='edit_student'),
    path('student/<int:student_id>/documents/', views.document_vault, name='document_vault'),
    path('student/<int:student_id>/documents/delete/<int:doc_id>/', views.delete_document, name='delete_document'),
    
    # USER MANAGEMENT (Admin only)
    path('manage-users/', views.manage_users, name='manage_users'),
    path('approve-user/<int:user_id>/', views.approve_user, name='approve_user'),
    path('suspend-user/<int:user_id>/', views.suspend_user, name='suspend_user'),
    path('unsuspend-user/<int:user_id>/', views.unsuspend_user, name='unsuspend_user'),
    path('delete-user/<int:user_id>/', views.delete_user_permanent, name='delete_user_permanent'),
    path('ban-user/<int:user_id>/', views.ban_user_permanent, name='ban_user_permanent'),
    
    # USER PROFILE
    path('profile/', views.user_profile_settings, name='user_profile_settings'),
    path('profile/<int:user_id>/', views.view_user_profile, name='view_user_profile'),
    path('admin-profile/', views.admin_profile, name='admin_profile'),
    path('upload-profile-picture/', views.upload_profile_picture, name='upload_profile_picture'),
    
    # AI RECOMMENDATION SYSTEM
    path('ai/dashboard/', views.ai_dashboard, name='ai_dashboard'),
    path('ai/api/global/recommendations/', views.global_recommendations, name='global_recommendations'),
    path('ai/api/student/<int:student_id>/recommendations/', views.student_recommendations, name='student_recommendations'),
    path('ai/api/class/<int:class_id>/recommendations/', views.class_recommendations, name='class_recommendations'),
    path('ai/api/all-students/', views.ai_all_students, name='ai_all_students'),
    path('ai/api/risk-stats/', views.ai_risk_stats, name='ai_risk_stats'),
    path('ai/api/trend-analysis/', views.ai_trend_analysis, name='ai_trend_analysis'),
    path('ai/api/intervention-suggestions/<int:student_id>/', views.ai_intervention_suggestions, name='ai_intervention_suggestions'),
    path('ai/api/bulk-risk-update/', views.ai_bulk_risk_update, name='ai_bulk_risk_update'),
    path('ai/api/predictive-analysis/', views.ai_predictive_analysis, name='ai_predictive_analysis'),
    path('ai/api/behavior-patterns/', views.ai_behavior_patterns, name='ai_behavior_patterns'),
    path('ai/api/intervention-effectiveness/', views.ai_intervention_effectiveness, name='ai_intervention_effectiveness'),
    
    # AI CHAT
    path('ai/chat/', views.ai_chat_page, name='ai_chat_page'),
    path('ai/chat/api/', views.ai_chat_api, name='ai_chat_api'),
    path('ai/chat/student-context/', views.ai_chat_student_context, name='ai_chat_student_context'),
    
    # AETHER AI
    path('aether/chat/api/', views.aether_chat_api, name='aether_chat_api'),
    
    # API ENDPOINTS
    path('api/search-students/', views.search_students_api, name='search_students_api'),
    path('api/student-by-name/', views.student_by_name, name='student_by_name'),
    path('api/student-by-admission/<str:admission_number>/', views.student_by_admission, name='student_by_admission'),
    path('api/teacher-profile/<int:user_id>/', views.teacher_profile_api, name='teacher_profile_api'),
    path('api/report-error/', views.report_error, name='report_error'),
    path('api/notifications/', views.get_notifications_api, name='get_notifications_api'),
    path('api/notifications/read/<int:notification_id>/', views.mark_notification_read, name='mark_notification_read'),
    path('api/notifications/read-all/', views.mark_all_notifications_read, name='mark_all_notifications_read'),
    path('api/online-teachers/', views.online_teachers_api, name='online_teachers_api'),
    path('api/admin-notifications/', views.get_admin_notifications, name='get_admin_notifications'),
    path('api/dashboard-stats/', views.dashboard_stats_api, name='dashboard_stats_api'),
    path('api/streams/', views.get_streams_api, name='get_streams_api'),
    path('api/categories/', views.get_categories_api, name='get_categories_api'),
    path('api/qr-checkin/', views.api_qr_checkin, name='api_qr_checkin'),
    path('api/award-points/<int:student_id>/', views.award_points, name='award_points'),
    path('api/award-reward/<int:student_id>/', views.award_reward, name='award_reward'),
    path('api/test/', views.api_test_page, name='api_test_page'),
    
    # REST API (new)
    path('rest/students/', api_students_list, name='api_students_list'),
    path('rest/students/create/', api_create_student, name='api_create_student'),
    path('rest/students/<int:student_id>/', api_student_detail, name='api_student_detail'),
    path('rest/students/<int:student_id>/update/', api_update_student, name='api_update_student'),
    path('rest/students/<int:student_id>/delete/', api_delete_student, name='api_delete_student'),
    path('rest/reports/', api_reports_list, name='api_reports_list'),
    path('rest/reports/create/', api_create_report, name='api_create_report'),
    path('rest/attendance/', api_attendance_list, name='api_attendance_list'),
    path('rest/attendance/mark/', api_mark_attendance, name='api_mark_attendance'),
    path('rest/timetable/', api_timetable_list, name='api_timetable_list'),
    path('rest/timetable/create/', api_create_timetable_entry, name='api_create_timetable_entry'),
    path('rest/leaderboard/', api_leaderboard, name='api_leaderboard'),
    path('rest/rewards/', api_rewards_list, name='api_rewards_list'),
    path('rest/points/<int:student_id>/', api_student_points, name='api_student_points'),
    path('rest/documents/<int:student_id>/', api_documents_list, name='api_documents_list'),
    path('rest/documents/<int:student_id>/upload/', api_upload_document, name='api_upload_document'),
    path('rest/analytics/overview/', api_analytics_overview, name='api_analytics_overview'),
    path('rest/analytics/categories/', api_category_distribution, name='api_category_distribution'),
    path('rest/analytics/trends/', api_trend_analysis, name='api_trend_analysis'),
    path('rest/parent/children/', api_parent_children, name='api_parent_children'),
    path('rest/parent/notifications/', api_parent_notifications, name='api_parent_notifications'),
    path('rest/dashboard/', api_dashboard_data, name='api_dashboard_data'),
    path('rest/health/', api_health, name='api_health'),
    path('rest/login/', api_login, name='api_login'),
    path('rest/logout/', api_logout, name='api_logout'),
    
    # REPORT MANAGEMENT
    path('reports/export/<str:format>/', views.export_reports, name='export_reports'),
    path('reports/student/<int:student_id>/export/', views.export_student_reports, name='export_student_reports'),
    
    # PARENT PORTAL
    path('parent-portal/', views.parent_portal, name='parent_portal'),
    path('parent-portal/student/<int:student_id>/', views.parent_student_detail, name='parent_student_detail'),
    
    # SCHOOL MANAGEMENT
    path('manage-subjects/', views.manage_subjects, name='manage_subjects'),

    # WORKFLOW: approvals, resolution, sanctions, appeals, communications
    path('report/<int:report_id>/review/', views.review_report, name='review_report'),
    path('report/<int:report_id>/resolve/', views.resolve_report, name='resolve_report'),
    path('report/<int:report_id>/sanction/add/', views.add_sanction, name='add_sanction'),
    path('sanction/<int:sanction_id>/update/', views.update_sanction, name='update_sanction'),
    path('student/<int:student_id>/appeal/add/', views.file_appeal, name='file_appeal'),
    path('appeal/<int:appeal_id>/review/', views.review_appeal, name='review_appeal'),
    path('student/<int:student_id>/communicate/', views.communicate_parent, name='communicate_parent'),
    path('student/<int:student_id>/history/', views.student_history, name='student_history'),
    path('api/report/<int:report_id>/history/', views.report_history_api, name='report_history_api'),
]