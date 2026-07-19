from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Stream, TeacherProfile, UserSession,
    Student, DisciplineCategory, DisciplineReport, Notification,
    Subject, TimetableEntry, AttendanceRecord,
    ParentProfile, StudentParentLink, StudentPoints, Reward,
    StudentReward, LeaderboardEntry, DocumentVault, UserRole,
    School, GradeLevel, AcademicTerm,
)


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ['name', 'short_name', 'current_year', 'terms_per_year', 'allow_teacher_registration']
    search_fields = ['name', 'short_name']


@admin.register(GradeLevel)
class GradeLevelAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'order', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name', 'code']


@admin.register(AcademicTerm)
class AcademicTermAdmin(admin.ModelAdmin):
    list_display = ['name', 'term_number', 'year', 'start_date', 'end_date', 'is_current', 'is_active']
    list_filter = ['year', 'is_current', 'is_active']


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'created_at']
    search_fields = ['name', 'code']
    list_filter = ['is_active']


@admin.register(Stream)
class StreamAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'is_active', 'created_at']
    search_fields = ['name', 'code']
    list_filter = ['is_active', 'school']


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'assigned_stream', 'assigned_form', 'is_online', 'is_approved', 'is_suspended']
    list_filter = ['is_online', 'is_approved', 'is_suspended', 'assigned_stream']
    search_fields = ['user__username', 'user__email']


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ['user', 'login_time', 'last_activity', 'is_active']
    list_filter = ['is_active']
    search_fields = ['user__username']


@admin.register(DisciplineCategory)
class DisciplineCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'key', 'default_rating', 'is_active', 'order']
    list_filter = ['is_active', 'default_rating']
    search_fields = ['name', 'key']
    list_editable = ['default_rating', 'is_active', 'order']
    ordering = ['order', 'name']


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['admission_number', 'name', 'stream', 'form', 'risk_score_display', 'year']
    list_filter = ['stream', 'form', 'year']
    search_fields = ['admission_number', 'name']
    readonly_fields = ['risk_score', 'created_at']

    def risk_score_display(self, obj):
        color = 'red' if obj.risk_score >= 60 else 'orange' if obj.risk_score >= 30 else 'green'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}%</span>', color, obj.risk_score
        )
    risk_score_display.short_description = 'Risk Score'


@admin.register(DisciplineReport)
class DisciplineReportAdmin(admin.ModelAdmin):
    list_display = ['student', 'category', 'reported_by', 'points', 'rating', 'reported_at']
    list_filter = ['category', 'rating', 'reported_at']
    search_fields = ['student__name', 'category__name']
    readonly_fields = ['category_name', 'points']


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'notification_type', 'created_at', 'is_read']
    list_filter = ['notification_type', 'is_read']
    search_fields = ['title']


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ['student', 'stream', 'date', 'status', 'checkin_method', 'check_in_time']
    list_filter = ['status', 'checkin_method', 'date']
    search_fields = ['student__name']
    date_hierarchy = 'date'


@admin.register(TimetableEntry)
class TimetableEntryAdmin(admin.ModelAdmin):
    list_display = ['stream', 'form', 'subject', 'day', 'start_time', 'end_time', 'room']
    list_filter = ['day', 'stream', 'form']
    search_fields = ['stream__name', 'subject__name', 'room']


@admin.register(ParentProfile)
class ParentProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'phone_number', 'email', 'receive_sms', 'receive_email', 'is_verified']
    search_fields = ['user__username', 'phone_number', 'email']
    list_filter = ['is_verified']


@admin.register(StudentParentLink)
class StudentParentLinkAdmin(admin.ModelAdmin):
    list_display = ['student', 'parent', 'relationship', 'is_primary', 'can_pickup']
    search_fields = ['student__name', 'parent__user__username']


@admin.register(StudentPoints)
class StudentPointsAdmin(admin.ModelAdmin):
    list_display = ['student', 'points', 'reason', 'created_at']
    list_filter = ['reason', 'created_at']
    search_fields = ['student__name']
    date_hierarchy = 'created_at'


@admin.register(Reward)
class RewardAdmin(admin.ModelAdmin):
    list_display = ['name', 'reward_type', 'points_required', 'is_active', 'icon']
    list_filter = ['reward_type', 'is_active']
    search_fields = ['name']


@admin.register(StudentReward)
class StudentRewardAdmin(admin.ModelAdmin):
    list_display = ['student', 'reward', 'awarded_by', 'created_at']
    search_fields = ['student__name', 'reward__name']


@admin.register(LeaderboardEntry)
class LeaderboardEntryAdmin(admin.ModelAdmin):
    list_display = ['student', 'period', 'period_start', 'period_end', 'total_points', 'rank']
    list_filter = ['period', 'school']
    ordering = ['-total_points', 'rank']


@admin.register(DocumentVault)
class DocumentVaultAdmin(admin.ModelAdmin):
    list_display = ['student', 'document_type', 'title', 'is_confidential', 'uploaded_by', 'created_at']
    list_filter = ['document_type', 'is_confidential']
    search_fields = ['student__name', 'title']
    readonly_fields = ['file_size', 'mime_type']


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'department', 'is_active']
    list_filter = ['role', 'is_active']
    search_fields = ['user__username', 'user__email']