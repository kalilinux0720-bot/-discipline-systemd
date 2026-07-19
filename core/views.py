# ============================================
# DJANGO CORE IMPORTS
# ============================================
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import never_cache
from django.utils.crypto import get_random_string

# ============================================
# DATABASE & QUERY IMPORTS
# ============================================
from django.db.models import Count, Q, Avg, Max, Min, Sum
from django.db.models.functions import ExtractWeekDay

# ============================================
# PYTHON STANDARD LIBRARY
# ============================================
import json
import os
from datetime import datetime, timedelta

# ============================================
# THIRD PARTY IMPORTS
# ============================================
import pandas as pd

# ============================================
# LOCAL MODELS
# ============================================
from .models import (
    Student,
    DisciplineReport,
    DisciplineCategory,
    Notification,
    Stream,
    TeacherProfile,
    UserSession,
    PasswordReset,
    School,
    GradeLevel,
    AcademicTerm,
    RateLimitLog,
    ReportHistory,
    Sanction,
    Appeal,
    CommunicationLog,
)
from .models import create_admin_notification, create_user_notification
# ============================================
# HELPERS
# ============================================

def admin_required(user):
    return user.is_superuser


def class_teacher_required(user):
    return user.is_superuser or user.groups.filter(name='ClassTeacher').exists()


def get_teacher_profile(user):
    try:
        return user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return None


def get_rating_label(rating):
    labels = {
        'VERY_MINOR': 'Very Minor',
        'MINOR': 'Minor',
        'MODERATE': 'Moderate',
        'SERIOUS': 'Serious',
        'VERY_SERIOUS': 'Very Serious',
    }
    return labels.get(rating, 'Moderate')


def _create_report(request, student, post):
    """
    Creates a DisciplineReport for the given student using POST data.
    Returns True on success, False on failure (messages already set).
    """
    category_id = post.get('category') or post.get('category_id')
    report_type = post.get('report_type', 'standard')
    custom_case = post.get('custom_case', '').strip()
    comments = post.get('comments', '')
    rating = post.get('rating', 'MODERATE')

    if report_type == 'custom' or category_id == 'custom':
        if not custom_case:
            messages.error(request, 'Please describe the custom case.')
            return False
        try:
            category, _ = DisciplineCategory.objects.get_or_create(
                key='CUSTOM',
                defaults={
                    'name': 'Custom Case',
                    'description': 'User-defined custom offense',
                    'default_rating': 'MODERATE',
                    'is_active': True,
                    'order': 999,
                }
            )
            category_name = custom_case
        except Exception as e:
            messages.error(request, f'Error creating custom category: {e}')
            return False
    else:
        if not category_id:
            messages.error(request, 'Please select an offence category.')
            return False
        try:
            category = DisciplineCategory.objects.get(id=category_id, is_active=True)
            category_name = category.name
        except DisciplineCategory.DoesNotExist:
            messages.error(request, 'Invalid category selected.')
            return False

    valid_ratings = ['VERY_MINOR', 'MINOR', 'MODERATE', 'SERIOUS', 'VERY_SERIOUS']
    if rating not in valid_ratings:
        rating = getattr(category, 'default_rating', 'MODERATE')

    report_comments = f"{comments}\n[Custom: {custom_case}]" if custom_case else comments

    report = DisciplineReport.objects.create(
        student=student,
        reported_by=request.user,
        category=category,
        comments=report_comments,
        rating=rating,
    )

    if custom_case:
        report.category_name = custom_case
        report.save(update_fields=['category_name'])

    # Rate limiting / anomaly flagging: throttle per teacher->student per day
    today = timezone.now().date()
    day_count = RateLimitLog.objects.filter(
        reported_by=request.user, student=student,
        reported_at__date=today,
    ).count()
    flagged = day_count >= 3  # same teacher files 3+ on one student same day
    RateLimitLog.objects.create(
        reported_by=request.user, student=student, flagged=flagged
    )
    if flagged:
        create_admin_notification(
            f'🚨 Anomaly: {request.user.username} on {student.name}',
            f'{day_count + 1} reports filed by {request.user.username} against '
            f'{student.name} today. Please review for potential abuse.',
            'critical', student
        )

    rating_label = get_rating_label(rating)
    if report.requires_approval:
        messages.success(
            request,
            f'? Report submitted for {student.name}: {category_name} '
            f'(Points: +{report.points}, Rating: {rating_label}). '
            f'⏳ Awaiting second staff approval before it is finalized.'
        )
    else:
        messages.success(
            request,
            f'? Report submitted for {student.name}: {category_name} '
            f'(Points: +{report.points}, Rating: {rating_label})'
        )
    return True


def _get_form_choices():
    """Return grade-level form choices, falling back to Student.FORM_CHOICES."""
    active_grades = GradeLevel.objects.filter(is_active=True).order_by('order', 'name')
    if active_grades.exists():
        return [(g.name, g.name) for g in active_grades]
    return Student.FORM_CHOICES


# ============================================
# AUTHENTICATION VIEWS
# ============================================

@csrf_exempt
@never_cache
def custom_login(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if not user.is_active:
                messages.error(request, 'Account permanently banned. Contact admin.')
                return render(request, 'login.html', {'error': 'Account permanently banned'})

            profile = get_teacher_profile(user)
            if profile:
                if not profile.is_approved and user.groups.filter(name='ClassTeacher').exists():
                    messages.error(request, 'Account pending admin approval.')
                    return render(request, 'login.html', {'error': 'Account pending approval'})
                if profile.is_suspended:
                    messages.error(request, f'Account suspended. Reason: {profile.suspension_reason}')
                    return render(request, 'login.html', {'error': 'Account suspended'})

            login(request, user)
            
            # Save session before accessing session_key
            request.session.save()
            
            # Create user session with valid session_key
            UserSession.objects.create(
                user=user,
                session_key=request.session.session_key,
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            )

            if profile:
                profile.is_online = True
                profile.last_activity = timezone.now()
                profile.save(update_fields=['is_online', 'last_activity'])

            if user.groups.filter(name='ClassTeacher').exists():
                if not profile or not profile.has_chosen_stream:
                    return redirect('choose_stream')

            return redirect('dashboard')

        messages.error(request, 'Invalid username or password.')
        return render(request, 'login.html', {'error': 'Invalid credentials'})

    return render(request, 'login.html')


def custom_logout(request):
    if request.user.is_authenticated:
        profile = get_teacher_profile(request.user)
        if profile:
            profile.is_online = False
            profile.save(update_fields=['is_online'])
        UserSession.objects.filter(user=request.user, is_active=True).update(is_active=False)
        logout(request)
    return redirect('login')


@csrf_exempt
@never_cache
def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        password2 = request.POST.get('password2', '')
        full_name = request.POST.get('full_name', '').strip()
        email = request.POST.get('email', '').strip()
        role = request.POST.get('role', 'teacher')

        if not username:
            messages.error(request, 'Username is required.')
            return render(request, 'register.html', {'error': 'Username required'})

        if password != password2:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'register.html', {'error': 'Passwords do not match'})

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists.')
            return render(request, 'register.html', {'error': 'Username already exists'})

        user = User.objects.create_user(username=username, password=password, email=email)
        if full_name:
            name_parts = full_name.split()
            user.first_name = name_parts[0]
            user.last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''
            user.save()

        if role == 'class_teacher':
            group, _ = Group.objects.get_or_create(name='ClassTeacher')
            user.groups.add(group)
            profile, _ = TeacherProfile.objects.get_or_create(user=user)
            profile.is_approved = False
            profile.is_suspended = False
            profile.has_chosen_stream = False
            profile.save()
            messages.success(request, 'Registration successful! Waiting for admin approval.')
        else:
            group, _ = Group.objects.get_or_create(name='Teacher')
            user.groups.add(group)
            messages.success(request, 'Registration successful! You can now login.')

        return redirect('login')

    return render(request, 'register.html')


@login_required
def choose_stream(request):
    if not request.user.groups.filter(name='ClassTeacher').exists():
        return redirect('dashboard')

    profile = get_teacher_profile(request.user)
    if not profile:
        profile = TeacherProfile.objects.create(user=request.user, has_chosen_stream=False)

    if profile.has_chosen_stream:
        return redirect('dashboard')

    if request.method == 'POST':
        stream_id = request.POST.get('stream')
        form = request.POST.get('form')
        if stream_id:
            try:
                stream = Stream.objects.get(id=stream_id, is_active=True)
                profile.assigned_stream = stream
                profile.assigned_form = form
                profile.has_chosen_stream = True
                profile.is_approved = False
                profile.save()
                messages.success(request, 'Stream assigned! Waiting for admin approval.')
                return redirect('dashboard')
            except Stream.DoesNotExist:
                messages.error(request, 'Invalid stream selected.')
        else:
            messages.error(request, 'Please select a stream.')

    return render(request, 'choose_stream.html', {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'forms': _get_form_choices(),
    })


@login_required
def dashboard_redirect(request):
    if request.user.is_superuser:
        return redirect('admin_dashboard')
    elif request.user.groups.filter(name='ClassTeacher').exists():
        return redirect('class_teacher_dashboard')
    else:
        return redirect('teacher_dashboard')

# ============================================
# ADMIN DASHBOARD
# ============================================

@login_required
@user_passes_test(admin_required)
def admin_dashboard(request):
    # -- Statistics --
    total_students = Student.objects.filter(is_active=True).count()
    total_reports = DisciplineReport.objects.count()
    reports_today = DisciplineReport.objects.filter(
        reported_at__date=timezone.now().date()
    ).count()

    good_count = Student.objects.filter(is_active=True, risk_score__lt=30).count()
    warning_count = Student.objects.filter(is_active=True, risk_score__range=(30, 59)).count()
    critical_count = Student.objects.filter(is_active=True, risk_score__gte=60).count()

    good_percentage = round(good_count / total_students * 100, 1) if total_students else 0
    warning_percentage = round(warning_count / total_students * 100, 1) if total_students else 0
    critical_percentage = round(critical_count / total_students * 100, 1) if total_students else 0

    high_report_students = Student.objects.filter(is_active=True).annotate(
        report_count=Count('reports')
    ).filter(report_count__gte=20).select_related('stream')

    online_teachers = TeacherProfile.objects.filter(is_online=True).select_related('user')
    online_count = online_teachers.count()

    recent_reports = DisciplineReport.objects.select_related(
        'student', 'reported_by', 'category'
    ).order_by('-reported_at')[:50]

    unread_notifications = request.user.notifications.filter(is_read=False)
    notification_count = unread_notifications.count()

    # -- Filters & Search --
    search_query = request.GET.get('search', '').strip()
    stream_filter = request.GET.get('stream', '').strip()
    form_filter = request.GET.get('form', '').strip()
    risk_filter = request.GET.get('risk', '').strip()

    students_qs = Student.objects.filter(is_active=True).select_related('stream')

    if search_query:
        students_qs = students_qs.filter(
            Q(name__icontains=search_query) | Q(admission_number__icontains=search_query)
        )
    if stream_filter:
        students_qs = students_qs.filter(stream__name=stream_filter)
    if form_filter:
        students_qs = students_qs.filter(form=form_filter)
    if risk_filter == 'critical':
        students_qs = students_qs.filter(risk_score__gte=60)
    elif risk_filter == 'warning':
        students_qs = students_qs.filter(risk_score__range=(30, 59))
    elif risk_filter == 'good':
        students_qs = students_qs.filter(risk_score__lt=30)

    paginator = Paginator(students_qs.order_by('name'), 20)
    students_page = paginator.get_page(request.GET.get('page'))

    # -- POST Actions --
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_student':
            try:
                stream = get_object_or_404(Stream, id=request.POST.get('stream'), is_active=True)
                Student.objects.create(
                    admission_number=request.POST.get('admission_number', '').strip(),
                    name=request.POST.get('name', '').strip(),
                    stream=stream,
                    form=request.POST.get('form', ''),
                    year=int(request.POST.get('year', timezone.now().year)),
                    optional_notes=request.POST.get('optional_notes', ''),
                    created_by=request.user,
                )
                messages.success(request, '? Student added successfully.')
            except Exception as e:
                messages.error(request, f'? Error adding student: {e}')
            return redirect('admin_dashboard')

        elif action == 'delete_student':
            student_id = request.POST.get('student_id')
            updated = Student.objects.filter(id=student_id).update(is_active=False)
            if updated:
                messages.success(request, '? Student deactivated successfully.')
            else:
                messages.error(request, '? Student not found.')
            return redirect('admin_dashboard')

        elif action == 'report_student':
            student_id = request.POST.get('student_id')
            student = get_object_or_404(Student, id=student_id, is_active=True)
            _create_report(request, student, request.POST)
            return redirect('admin_dashboard')

    # -- Context --
    stream_choices = [(s.id, s.name) for s in Stream.objects.filter(is_active=True).order_by('name')]
    categories = DisciplineCategory.objects.filter(is_active=True).order_by('order', 'name')

    context = {
        'streams_list': Stream.objects.filter(is_active=True).order_by('name'),
        'students': students_page,
        'total_students': total_students,
        'critical_students': critical_count,
        'total_reports': total_reports,
        'reports_today': reports_today,
        'high_report_students': high_report_students,
        'online_teachers': online_teachers,
        'online_count': online_count,
        'recent_reports': recent_reports,
        'notification_count': notification_count,
        'unread_notifications': unread_notifications,
        'streams': stream_choices,
        'forms': _get_form_choices(),
        'categories': categories,
        'search_query': search_query,
        'stream_filter': stream_filter,
        'form_filter': form_filter,
        'risk_filter': risk_filter,
        'good_count': good_count,
        'warning_count': warning_count,
        'critical_count': critical_count,
        'good_percentage': good_percentage,
        'warning_percentage': warning_percentage,
        'critical_percentage': critical_percentage,
    }
    return render(request, 'admin_dashboard.html', context)


# ============================================
# CLASS TEACHER DASHBOARD
# ============================================

@login_required
@user_passes_test(class_teacher_required)
def class_teacher_dashboard(request):
    profile = get_teacher_profile(request.user)

    if not profile or not profile.has_chosen_stream or not profile.assigned_stream_id:
        return redirect('choose_stream')

    # Verify assigned stream still exists and is active
    try:
        assigned_stream = Stream.objects.get(id=profile.assigned_stream_id, is_active=True)
    except Stream.DoesNotExist:
        profile.assigned_stream = None
        profile.has_chosen_stream = False
        profile.is_approved = False
        profile.save()
        messages.error(request, '? Your assigned stream was deleted. Please choose a new stream.')
        return redirect('choose_stream')

    assigned_form = profile.assigned_form

    # -- POST Actions --
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'report_student':
            student_id = request.POST.get('student_id')
            student = get_object_or_404(Student, id=student_id, is_active=True)
            _create_report(request, student, request.POST)
            return redirect('class_teacher_dashboard')

        elif action == 'add_student':
            try:
                Student.objects.create(
                    admission_number=request.POST.get('admission_number', '').strip(),
                    name=request.POST.get('name', '').strip(),
                    stream=assigned_stream,
                    form=request.POST.get('form', assigned_form or ''),
                    year=int(request.POST.get('year', timezone.now().year)),
                    optional_notes=request.POST.get('optional_notes', ''),
                    created_by=request.user,
                )
                messages.success(request, f'? Student added to {assigned_stream.name}!')
            except Exception as e:
                messages.error(request, f'? Error adding student: {e}')
            return redirect('class_teacher_dashboard')

    # -- Student Queryset --
    display_students = Student.objects.filter(
        is_active=True, stream=assigned_stream
    ).select_related('stream').order_by('name')

    if assigned_form:
        display_students = display_students.filter(form=assigned_form)

    # -- Class Statistics --
    stream_students = Student.objects.filter(is_active=True, stream=assigned_stream)
    total_students = stream_students.count()
    critical_count = stream_students.filter(risk_score__gte=60).count()
    warning_count = stream_students.filter(risk_score__range=(30, 59)).count()
    good_count = stream_students.filter(risk_score__lt=30).count()
    avg_risk = stream_students.aggregate(avg=Avg('risk_score'))['avg'] or 0

    good_percentage = round(good_count / total_students * 100, 1) if total_students else 0
    warning_percentage = round(warning_count / total_students * 100, 1) if total_students else 0
    critical_percentage = round(critical_count / total_students * 100, 1) if total_students else 0

    class_stats = {
        'total': total_students,
        'critical': critical_count,
        'warning': warning_count,
        'good': good_count,
        'avg_risk': round(avg_risk, 1),
    }

    paginator = Paginator(display_students, 20)
    students_page = paginator.get_page(request.GET.get('page'))

    my_reports = DisciplineReport.objects.filter(
        student__stream=assigned_stream
    ).select_related('student', 'reported_by', 'category').order_by('-reported_at')[:30]

    notifications = request.user.notifications.filter(is_read=False).order_by('-created_at')[:20]
    notification_count = notifications.count()

    context = {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'students': students_page,
        'assigned_stream': assigned_stream,
        'assigned_form': assigned_form,
        'class_stats': class_stats,
        'total_students': total_students,
        'critical_students': critical_count,
        'good_count': good_count,
        'warning_count': warning_count,
        'critical_count': critical_count,
        'good_percentage': good_percentage,
        'warning_percentage': warning_percentage,
        'critical_percentage': critical_percentage,
        'my_reports': my_reports,
        'notification_count': notification_count,
        'notifications': notifications,
        'categories': DisciplineCategory.objects.filter(is_active=True).order_by('order', 'name'),
        'forms': _get_form_choices(),
        'profile': profile,
    }
    return render(request, 'class_teacher_dashboard.html', context)


# ============================================
# TEACHER DASHBOARD
# ============================================

@login_required
def teacher_dashboard(request):
    """
    Teacher dashboard view - shows all students with filtering and reporting capabilities
    """
    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    stream_filter = request.GET.get('stream', '').strip()
    form_filter = request.GET.get('form', '').strip()
    
    # Get streams for dropdown
    streams = Stream.objects.filter(is_active=True).order_by('name')
    
    # Base queryset
    students_qs = Student.objects.filter(is_active=True).select_related('stream').order_by('name')
    
    # Apply filters
    if search_query:
        students_qs = students_qs.filter(
            Q(name__icontains=search_query) | Q(admission_number__icontains=search_query)
        )
    if stream_filter:
        students_qs = students_qs.filter(stream__name=stream_filter)
    if form_filter:
        students_qs = students_qs.filter(form=form_filter)
    
    # Pagination
    paginator = Paginator(students_qs, 20)
    students_page = paginator.get_page(request.GET.get('page'))
    
    # Handle POST - Report Student
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'report_student':
            student_id = request.POST.get('student_id')
            if student_id:
                student = get_object_or_404(Student, id=student_id, is_active=True)
                _create_report(request, student, request.POST)
                return redirect('teacher_dashboard')
    
    # Get unread notifications
    unread_notifications = request.user.notifications.filter(is_read=False)
    notification_count = unread_notifications.count()
    
    # Get categories for report form
    categories = DisciplineCategory.objects.filter(is_active=True).order_by('order', 'name')
    
    # Get form choices
    forms = _get_form_choices()
    
    # Build context
    context = {
        'streams': streams,
        'students': students_page,
        'categories': categories,
        'forms': forms,
        'search_query': search_query,
        'stream_filter': stream_filter,
        'form_filter': form_filter,
        'notification_count': notification_count,
        'unread_notifications': unread_notifications,
    }
    
    return render(request, 'teacher_dashboard.html', context)
# ============================================
# STUDENT PROFILE
# ============================================

@login_required
def student_profile(request, student_id):
    student = get_object_or_404(Student, id=student_id, is_active=True)
    reports = student.reports.select_related('reported_by', 'category').order_by('-reported_at')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'report_student':
            _create_report(request, student, request.POST)
            return redirect('student_profile', student_id=student_id)

    category_breakdown = reports.values('category__name').annotate(
        count=Count('id')
    ).order_by('-count')

    unread_notifications = request.user.notifications.filter(is_read=False)

    context = {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'student': student,
        'reports': reports,
        'categories': DisciplineCategory.objects.filter(is_active=True).order_by('order', 'name'),
        'category_breakdown': category_breakdown,
        'total_reports': reports.count(),
        'notification_count': unread_notifications.count(),
        'unread_notifications': unread_notifications,
    }
    return render(request, 'student_profile.html', context)


# ============================================
# TEACHER PROFILE API
# ============================================

@login_required
def teacher_profile_api(request, user_id):
    """
    API endpoint to get teacher profile data for quick view modal
    """
    try:
        user = get_object_or_404(User, id=user_id)
        profile = get_teacher_profile(user)
        
        # Get user groups
        groups = user.groups.values_list('name', flat=True)
        is_class_teacher = 'ClassTeacher' in groups
        is_teacher = 'Teacher' in groups
        
        # Get full profile picture URL
        profile_picture_url = None
        if profile and profile.profile_picture:
            try:
                profile_picture_url = request.build_absolute_uri(profile.profile_picture.url)
            except (ValueError, AttributeError, Exception):
                profile_picture_url = None
        
        data = {
            'id': user.id,
            'username': user.username,
            'full_name': user.get_full_name() or user.username,
            'email': user.email or '',
            'profile_picture': profile_picture_url,
            'is_online': profile.is_online if profile else False,
            'is_approved': profile.is_approved if profile else False,
            'is_suspended': profile.is_suspended if profile else False,
            'assigned_stream': profile.assigned_stream.name if profile and profile.assigned_stream else 'Not Assigned',
            'assigned_form': profile.assigned_form if profile else None,
            'phone': profile.phone_number if profile else '',
            'role': 'Class Teacher' if is_class_teacher else 'Teacher' if is_teacher else 'Admin',
            'is_superuser': user.is_superuser,
            'suspension_reason': profile.suspension_reason if profile and profile.is_suspended else None,
            'has_chosen_stream': profile.has_chosen_stream if profile else False,
            'last_activity': profile.last_activity.strftime('%Y-%m-%d %H:%M') if profile and profile.last_activity else None,
        }
        return JsonResponse(data)
        
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

# ============================================
# API ENDPOINTS
# ============================================

@login_required
def search_students_api(request):
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'students': []})

    students_qs = Student.objects.filter(
        Q(name__icontains=query) | Q(admission_number__icontains=query),
        is_active=True
    ).select_related('stream')[:20]

    # Allow PK lookup if numeric and nothing matched
    if not students_qs.exists() and query.isdigit():
        try:
            pk_student = Student.objects.get(id=int(query), is_active=True)
            students_qs = [pk_student]
        except Student.DoesNotExist:
            pass

    data = [
        {
            'id': s.id,
            'name': s.name,
            'admission_number': s.admission_number,
            'stream': s.stream.name if s.stream else 'N/A',
            'form': s.form,
            'risk_score': s.risk_score,
            'risk_level': s.risk_level,
            'is_critical': s.is_critical,
            'total_reports': s.total_reports,
        }
        for s in students_qs
    ]
    return JsonResponse({'students': data})


@login_required
def mark_notification_read(request, notification_id):
    if request.method == 'POST':
        notification = get_object_or_404(Notification, id=notification_id)
        notification.mark_read(request.user)
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error', 'message': 'Method not allowed.'}, status=405)


@login_required
def get_notifications_api(request):
    notifications = request.user.notifications.filter(is_read=False).order_by('-created_at')[:10]
    data = [
        {
            'id': n.id,
            'title': n.title,
            'message': n.message[:100],
            'type': n.notification_type,
            'created_at': n.created_at.strftime('%Y-%m-%d %H:%M'),
            'student_id': n.student.id if hasattr(n, 'student') and n.student else None,
        }
        for n in notifications
    ]
    return JsonResponse({'notifications': data, 'count': len(data)})


@login_required
def online_teachers_api(request):
    online = TeacherProfile.objects.filter(is_online=True).select_related('user', 'assigned_stream')
    data = [
        {
            'username': t.user.username,
            'full_name': t.user.get_full_name() or t.user.username,
            'assigned_stream': t.assigned_stream.name if t.assigned_stream else 'Not Assigned',
            'last_activity': t.last_activity.strftime('%Y-%m-%d %H:%M') if t.last_activity else 'N/A',
        }
        for t in online
    ]
    return JsonResponse({'online_teachers': data})


# ============================================
# ============================================
# AI VIEWS - ENHANCED VERSION
# ============================================

@login_required
def ai_dashboard(request):
    students = Student.objects.filter(is_active=True)
    unread_notifications = request.user.notifications.filter(is_read=False)
    
    # Get additional AI insights
    critical_students = students.filter(risk_level='CRITICAL')
    warning_students = students.filter(risk_level='WARNING')
    good_students = students.filter(risk_level='GOOD')
    
    # Calculate trend data
    total_students = students.count()
    critical_count = critical_students.count()
    warning_count = warning_students.count()
    good_count = good_students.count()
    
    # Get students with interventions
    students_with_interventions = students.filter(intervention_count__gt=0).count()
    
    # Get recent incidents
    recent_incidents = DisciplineReport.objects.filter(
        reported_at__date=timezone.now().date()
    ).count()

    context = {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'total_students': total_students,
        'critical_students': critical_count,
        'warning_students': warning_count,
        'good_students': good_count,
        'total_reports': DisciplineReport.objects.count(),
        'categories': DisciplineCategory.objects.filter(is_active=True).order_by('order', 'name'),
        'notification_count': unread_notifications.count(),
        'unread_notifications': unread_notifications,
        # New AI-specific context
        'students_with_interventions': students_with_interventions,
        'recent_incidents': recent_incidents,
        'avg_risk_score': students.aggregate(Avg('risk_score'))['risk_score__avg'] or 0,
        'critical_percentage': round((critical_count / total_students * 100) if total_students > 0 else 0, 1),
        'warning_percentage': round((warning_count / total_students * 100) if total_students > 0 else 0, 1),
        'good_percentage': round((good_count / total_students * 100) if total_students > 0 else 0, 1),
    }
    return render(request, 'ai_dashboard.html', context)


@login_required
def global_recommendations(request):
    try:
        students = Student.objects.filter(is_active=True)
        total_students = students.count()
        critical_count = students.filter(risk_level='CRITICAL').count()
        warning_count = students.filter(risk_level='WARNING').count()
        good_count = students.filter(risk_level='GOOD').count()
        total_reports = DisciplineReport.objects.count()
        avg_risk = students.aggregate(avg=Avg('risk_score'))['avg'] or 0

        recommendations = []

        # Critical risk recommendations
        critical_qs = students.filter(risk_level='CRITICAL').order_by('-risk_score')[:5]
        if critical_qs.exists():
            student_names = ', '.join([s.name for s in critical_qs[:3]])
            recommendations.append({
                'action': f'?? {critical_qs.count()} Critical Risk Students Detected',
                'details': f'Students: {student_names}. Immediate intervention needed.',
                'priority': 'HIGH',
                'steps': [
                    'Schedule parent-teacher meetings immediately',
                    'Assign school counselor for assessment',
                    'Implement behavior modification plan',
                    'Track intervention progress weekly'
                ],
            })

        # Warning level recommendations
        if warning_count > 0:
            warning_qs = students.filter(risk_level='WARNING').order_by('-risk_score')[:3]
            warning_names = ', '.join([s.name for s in warning_qs])
            recommendations.append({
                'action': f'?? {warning_count} Students at Warning Level',
                'details': f'Students: {warning_names}. Monitor these students closely to prevent escalation.',
                'priority': 'MEDIUM',
                'steps': [
                    'Increase monitoring and teacher attention',
                    'Contact parents for awareness',
                    'Document all incidents',
                    'Create individual improvement plans'
                ],
            })

        # Students with no interventions but risk present
        at_risk_no_intervention = students.filter(
            Q(risk_level='WARNING') | Q(risk_level='CRITICAL'),
            intervention_count=0
        ).count()
        if at_risk_no_intervention > 0:
            recommendations.append({
                'action': f'?? {at_risk_no_intervention} At-Risk Students Without Interventions',
                'details': 'These students need intervention plans but haven\'t received any yet.',
                'priority': 'HIGH' if at_risk_no_intervention > 5 else 'MEDIUM',
                'steps': [
                    'Schedule meetings with these students',
                    'Create intervention plans',
                    'Assign mentors or counselors',
                    'Document all interventions'
                ],
            })

        # Students with improving trends
        improving_students = students.filter(
            risk_level='WARNING',
            intervention_count__gt=0
        ).count()
        if improving_students > 0:
            recommendations.append({
                'action': f'?? {improving_students} Students Showing Improvement',
                'details': 'Students with interventions are showing positive trends.',
                'priority': 'LOW',
                'steps': [
                    'Continue current intervention strategies',
                    'Document success stories',
                    'Share best practices with staff',
                    'Maintain consistent monitoring'
                ],
            })

        # Check for students with no recent incidents
        no_recent_incidents = students.filter(
            last_incident_date__isnull=True
        ).count() if hasattr(Student, 'last_incident_date') else 0
        
        if no_recent_incidents > 0 and no_recent_incidents == total_students:
            recommendations.append({
                'action': '? All Students Doing Well',
                'details': f'All {total_students} students have no recent incidents.',
                'priority': 'LOW',
                'steps': [
                    'Continue positive reinforcement',
                    'Maintain regular monitoring',
                    'Celebrate student achievements',
                    'Document successful strategies'
                ],
            })

        # If no recommendations generated, add a default one
        if not recommendations:
            recommendations.append({
                'action': '?? School Discipline Status: Good',
                'details': f'All {total_students} students are managing well with an average risk of {avg_risk}%.',
                'priority': 'LOW',
                'steps': [
                    'Continue current positive practices',
                    'Maintain monitoring systems',
                    'Document successful strategies for future reference',
                ],
            })

        return JsonResponse({
            'recommendations': recommendations,
            'global_stats': {
                'total_students': total_students,
                'critical_count': critical_count,
                'warning_count': warning_count,
                'good_count': good_count,
                'total_reports': total_reports,
                'avg_risk': round(avg_risk, 1),
                'students_with_interventions': Student.objects.filter(intervention_count__gt=0).count(),
                'total_interventions': sum(s.intervention_count for s in students),
            },
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def student_recommendations(request, student_id):
    try:
        student = get_object_or_404(Student, id=student_id, is_active=True)
        reports = student.reports.select_related('category')
        total_reports = reports.count()

        # Get category breakdown
        category_breakdown = reports.values('category__name').annotate(
            count=Count('id')
        ).order_by('-count')

        student_data = {
            'id': student.id,
            'name': student.name,
            'admission_number': student.admission_number,
            'risk_score': student.risk_score,
            'risk_level': student.risk_level,
            'form': student.form,
            'stream': student.stream.name if student.stream else 'N/A',
            'total_reports': total_reports,
            'intervention_count': student.intervention_count,
            'days_since_last_incident': student.days_since_last_incident if hasattr(student, 'days_since_last_incident') else None,
            'risk_trend': student.risk_trend if hasattr(student, 'risk_trend') else 'stable',
        }

        recommendations = []

        # Risk level based recommendations
        if student.risk_level == 'CRITICAL':
            recommendations.append({
                'type': 'critical',
                'title': '?? CRITICAL  Immediate Action Required',
                'description': f'{student.name} has reached {student.risk_score}% risk level with {total_reports} total reports.',
                'steps': [
                    'Schedule emergency parent-teacher conference TODAY',
                    'Refer to school counselor immediately',
                    'Create comprehensive behavior intervention plan',
                    'Schedule weekly progress review meetings',
                    'Document all interventions'
                ]
            })
        elif student.risk_level == 'WARNING':
            recommendations.append({
                'type': 'warning',
                'title': '?? Warning  Monitor Closely',
                'description': f'{student.name} has {student.risk_score}% risk with {total_reports} reports.',
                'steps': [
                    'Schedule parent meeting within 1 week',
                    'Implement behavior tracking system',
                    'Assign mentor or buddy system',
                    'Increase classroom monitoring',
                    'Document all incidents'
                ]
            })
        else:
            recommendations.append({
                'type': 'success',
                'title': '? Student Status: Good',
                'description': f'{student.name} is doing well with {student.risk_score}% risk level.',
                'steps': [
                    'Continue positive reinforcement',
                    'Maintain regular monitoring',
                    'Encourage leadership opportunities',
                    'Document positive achievements'
                ]
            })

        # Pattern-based recommendations
        if total_reports > 0:
            for cat in category_breakdown:
                if cat['count'] >= 3:
                    recommendations.append({
                        'type': 'pattern',
                        'title': f'?? Pattern Detected: {cat["category__name"]}',
                        'description': f'{cat["count"]} reports in this category. Address this pattern.',
                        'steps': [
                            f'Discuss {cat["category__name"]} pattern with student',
                            'Develop specific strategies to address this issue',
                            'Monitor for improvement in this area',
                            'Document progress or setbacks'
                        ]
                    })
                elif cat['count'] >= 2:
                    recommendations.append({
                        'type': 'pattern',
                        'title': f'?? Emerging Pattern: {cat["category__name"]}',
                        'description': f'{cat["count"]} reports in this category. Monitor closely.',
                        'steps': [
                            f'Address {cat["category__name"]} with student',
                            'Track future incidents in this category',
                            'Consider preventive measures'
                        ]
                    })

        # Intervention recommendations
        if student.intervention_count == 0 and student.risk_level in ['WARNING', 'CRITICAL']:
            recommendations.append({
                'type': 'critical',
                'title': '?? No Interventions Recorded',
                'description': 'This student needs interventions but none have been recorded.',
                'steps': [
                    'Create intervention plan immediately',
                    'Schedule meeting with student and parents',
                    'Assign counselor or mentor',
                    'Document all interventions'
                ]
            })

        return JsonResponse({
            'student': student_data,
            'explain_risk': student.explain_risk(),
            'recommendations': recommendations,
            'total_reports': total_reports,
            'category_breakdown': list(category_breakdown),
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def class_recommendations(request, stream_id):
    try:
        stream = get_object_or_404(Stream, id=stream_id)
        students = Student.objects.filter(stream=stream, is_active=True)

        if not students.exists():
            return JsonResponse({
                'stream': stream.name,
                'statistics': {
                    'total_students': 0, 'critical_count': 0,
                    'warning_count': 0, 'good_count': 0, 'average_risk': 0,
                    'students_with_interventions': 0,
                },
                'recommendations': [{
                    'type': 'INFO',
                    'action': 'No students in this stream',
                    'details': 'Add students to this stream to get analysis.',
                }],
                'top_offenders': [],
            })

        total = students.count()
        critical = students.filter(risk_level='CRITICAL').count()
        warning = students.filter(risk_level='WARNING').count()
        good = students.filter(risk_level='GOOD').count()
        avg_risk = students.aggregate(avg=Avg('risk_score'))['avg'] or 0
        with_interventions = students.filter(intervention_count__gt=0).count()

        # Get top offenders (students with most reports)
        top_offenders = students.annotate(
            report_count=Count('reports')
        ).filter(report_count__gt=0).order_by('-report_count', '-risk_score')[:5]

        top_offenders_data = [
            {
                'name': s.name,
                'admission': s.admission_number,
                'risk_score': s.risk_score,
                'risk_level': s.risk_level,
                'reports': s.report_count,
                'interventions': s.intervention_count,
            }
            for s in top_offenders
        ]

        recommendations = []

        # Class-level recommendations
        if critical > 0:
            critical_students_list = students.filter(risk_level='CRITICAL').values_list('name', flat=True)[:5]
            critical_names = ', '.join(list(critical_students_list))
            recommendations.append({
                'type': 'URGENT',
                'action': f'?? {critical} students at CRITICAL risk in {stream.name}',
                'details': f'Students: {critical_names}. Immediate intervention needed for these students.',
                'steps': [
                    'Schedule individual meetings for each critical student',
                    'Create personalized intervention plans',
                    'Assign counselors to each student',
                    'Hold emergency parent-teacher conferences',
                    'Track progress weekly'
                ]
            })

        if warning > 3:
            recommendations.append({
                'type': 'WARNING',
                'action': f'?? {warning} students at WARNING level in {stream.name}',
                'details': 'Multiple students need monitoring to prevent escalation.',
                'steps': [
                    'Implement whole-class behavior monitoring',
                    'Contact parents of warning-level students',
                    'Increase teacher supervision',
                    'Create group intervention activities',
                    'Document all incidents and interventions'
                ]
            })
        elif warning > 0:
            warning_students_list = students.filter(risk_level='WARNING').values_list('name', flat=True)[:3]
            warning_names = ', '.join(list(warning_students_list))
            recommendations.append({
                'type': 'WARNING',
                'action': f'?? {warning} student(s) at WARNING level',
                'details': f'Students: {warning_names}. Monitor these students closely.',
                'steps': [
                    'Schedule individual meetings with each student',
                    'Create behavior tracking sheets',
                    'Contact parents for awareness',
                    'Document all interventions'
                ]
            })

        # Check for students without interventions
        no_intervention_risk = students.filter(
            Q(risk_level='WARNING') | Q(risk_level='CRITICAL'),
            intervention_count=0
        ).count()
        if no_intervention_risk > 0:
            recommendations.append({
                'type': 'URGENT',
                'action': f'?? {no_intervention_risk} at-risk students without interventions',
                'details': 'These students need interventions but none have been recorded.',
                'steps': [
                    'Create intervention plans immediately',
                    'Schedule meetings with each student',
                    'Assign mentors or counselors',
                    'Document all interventions'
                ]
            })

        # Positive recommendations
        if good == total and total > 0:
            recommendations.append({
                'type': 'SUCCESS',
                'action': f'? {stream.name} is performing excellently',
                'details': f'All {total} students have good risk levels.',
                'steps': [
                    'Maintain current positive practices',
                    'Recognize and celebrate class achievements',
                    'Document successful strategies',
                    'Share best practices with other streams'
                ]
            })

        # If no recommendations, add default
        if not recommendations:
            recommendations.append({
                'type': 'INFO',
                'action': f'?? {stream.name} Status: Stable',
                'details': f'{total} students with an average risk of {avg_risk}%. Continue monitoring.',
                'steps': [
                    'Maintain regular monitoring',
                    'Continue positive reinforcement',
                    'Document any incidents promptly',
                    'Keep communication channels open with parents'
                ]
            })

        return JsonResponse({
            'stream': stream.name,
            'statistics': {
                'total_students': total,
                'critical_count': critical,
                'warning_count': warning,
                'good_count': good,
                'average_risk': round(avg_risk, 1),
                'students_with_interventions': with_interventions,
                'total_interventions': sum(s.intervention_count for s in students),
            },
            'top_offenders': top_offenders_data,
            'recommendations': recommendations,
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def ai_all_students(request):
    try:
        students = Student.objects.filter(is_active=True).select_related('stream')
        total_reports = DisciplineReport.objects.count()

        all_students_data = []
        for student in students:
            all_students_data.append({
                'id': student.id,
                'name': student.name,
                'admission': student.admission_number,
                'stream': student.stream.name if student.stream else 'N/A',
                'form': student.form,
                'risk_score': student.risk_score,
                'risk_level': student.risk_level,
                'total_reports': student.reports.count(),
                'is_critical': student.risk_level == 'CRITICAL',
                'intervention_count': student.intervention_count,
                'days_since_incident': student.days_since_last_incident if hasattr(student, 'days_since_last_incident') else None,
            })

        total = len(all_students_data)
        critical = sum(1 for s in all_students_data if s['risk_level'] == 'CRITICAL')
        warning = sum(1 for s in all_students_data if s['risk_level'] == 'WARNING')
        good = sum(1 for s in all_students_data if s['risk_level'] == 'GOOD')
        avg_risk = sum(s['risk_score'] for s in all_students_data) / total if total else 0
        with_interventions = sum(1 for s in all_students_data if s['intervention_count'] > 0)

        insights = []
        if critical > 0:
            critical_students = [s['name'] for s in all_students_data if s['risk_level'] == 'CRITICAL'][:5]
            insights.append({
                'type': 'warning',
                'title': f'?? {critical} Students Need Immediate Attention',
                'students': critical_students,
                'action': 'Schedule parent meetings and counseling immediately',
                'priority': 'HIGH'
            })
        if warning > 0:
            warning_students = [s['name'] for s in all_students_data if s['risk_level'] == 'WARNING'][:5]
            insights.append({
                'type': 'info',
                'title': f'?? {warning} Students at Warning Level',
                'students': warning_students,
                'action': 'Increase monitoring and teacher attention',
                'priority': 'MEDIUM'
            })
        if with_interventions > 0:
            insights.append({
                'type': 'success',
                'title': f'? {with_interventions} Students Have Interventions',
                'students': [],
                'action': 'Continue monitoring intervention effectiveness',
                'priority': 'LOW'
            })

        return JsonResponse({
            'all_students': all_students_data,
            'total_reports': total_reports,
            'stats': {
                'total': total,
                'critical': critical,
                'warning': warning,
                'good': good,
                'avg_risk': round(avg_risk, 1),
                'with_interventions': with_interventions,
            },
            'insights': insights,
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def student_by_name(request):
    try:
        name = request.GET.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'No name provided.'}, status=400)

        student = Student.objects.filter(name__icontains=name, is_active=True).first()
        if not student:
            return JsonResponse({'error': 'Student not found.'}, status=404)

        # Get additional student data
        reports = student.reports.select_related('category')
        category_breakdown = reports.values('category__name').annotate(
            count=Count('id')
        ).order_by('-count')

        return JsonResponse({
            'id': student.id,
            'name': student.name,
            'admission_number': student.admission_number,
            'stream': student.stream.name if student.stream else 'N/A',
            'form': student.form,
            'risk_score': student.risk_score,
            'risk_level': student.risk_level,
            'total_reports': student.total_reports,
            'intervention_count': student.intervention_count,
            'days_since_incident': student.days_since_last_incident if hasattr(student, 'days_since_last_incident') else None,
            'category_breakdown': list(category_breakdown),
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================
# ADDITIONAL AI VIEWS
# ============================================

@login_required
def ai_risk_stats(request):
    """Get overall risk statistics for AI dashboard"""
    try:
        students = Student.objects.filter(is_active=True)
        total = students.count()
        
        stats = {
            'total_students': total,
            'critical_count': students.filter(risk_level='CRITICAL').count(),
            'warning_count': students.filter(risk_level='WARNING').count(),
            'good_count': students.filter(risk_level='GOOD').count(),
            'avg_risk_score': students.aggregate(Avg('risk_score'))['risk_score__avg'] or 0,
            'max_risk_score': students.aggregate(Max('risk_score'))['risk_score__max'] or 0,
            'min_risk_score': students.aggregate(Min('risk_score'))['risk_score__min'] or 0,
            'total_reports': DisciplineReport.objects.count(),
            'reports_today': DisciplineReport.objects.filter(
                reported_at__date=timezone.now().date()
            ).count(),
            'reports_this_week': DisciplineReport.objects.filter(
                reported_at__week=timezone.now().isocalendar()[1]
            ).count(),
            'total_interventions': sum(s.intervention_count for s in students),
            'students_with_interventions': students.filter(intervention_count__gt=0).count(),
        }
        
        # Get category breakdown
        category_breakdown = DisciplineReport.objects.values(
            'category__name'
        ).annotate(
            count=Count('id')
        ).order_by('-count')[:10]
        
        stats['top_categories'] = list(category_breakdown)
        
        return JsonResponse({'status': 'success', 'data': stats})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def ai_trend_analysis(request):
    """Get trend analysis for students"""
    try:
        from datetime import timedelta
        
        days = int(request.GET.get('days', 30))
        start_date = timezone.now() - timedelta(days=days)
        
        # Get daily report counts
        reports = DisciplineReport.objects.filter(
            reported_at__gte=start_date
        ).values(
            'reported_at__date'
        ).annotate(
            count=Count('id')
        ).order_by('reported_at__date')
        
        # Get risk level distribution
        total_students = Student.objects.filter(is_active=True).count()
        current_critical = Student.objects.filter(risk_level='CRITICAL').count()
        current_warning = Student.objects.filter(risk_level='WARNING').count()
        current_good = Student.objects.filter(risk_level='GOOD').count()
        
        # Get students with improving trends
        improving_students = Student.objects.filter(
            risk_level='WARNING',
            intervention_count__gt=0
        ).count()
        
        return JsonResponse({
            'status': 'success',
            'data': {
                'daily_reports': list(reports),
                'current_distribution': {
                    'critical': current_critical,
                    'warning': current_warning,
                    'good': current_good,
                    'total': total_students,
                },
                'improving_students': improving_students,
                'period_days': days,
                'total_reports_period': DisciplineReport.objects.filter(
                    reported_at__gte=start_date
                ).count(),
            }
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def ai_intervention_suggestions(request, student_id):
    """Get AI-powered intervention suggestions for a student"""
    try:
        student = get_object_or_404(Student, id=student_id, is_active=True)
        
        suggestions = []
        reports = student.reports.select_related('category')
        total_reports = reports.count()
        
        # Get category patterns
        category_patterns = reports.values('category__name').annotate(
            count=Count('id')
        ).filter(count__gte=2).order_by('-count')
        
        # Risk-based interventions
        if student.risk_level == 'CRITICAL':
            suggestions.append({
                'priority': 'URGENT',
                'action': 'Immediate Crisis Intervention',
                'description': f'Student has {student.risk_score}% risk. Requires immediate attention.',
                'steps': [
                    'Contact parents/guardians within 24 hours',
                    'Schedule emergency counseling session',
                    'Create behavior intervention plan',
                    'Document all interventions',
                    'Schedule weekly review meetings'
                ]
            })
            
        if student.risk_level == 'WARNING':
            suggestions.append({
                'priority': 'HIGH',
                'action': 'Preventive Intervention',
                'description': f'Student at {student.risk_score}% risk. Implement support systems.',
                'steps': [
                    'Schedule parent meeting within 1 week',
                    'Assign mentor or peer buddy',
                    'Create behavior tracking sheet',
                    'Monitor daily for 2 weeks',
                    'Document progress or setbacks'
                ]
            })
        
        # Pattern-based interventions
        for pattern in category_patterns:
            if pattern['count'] >= 3:
                suggestions.append({
                    'priority': 'HIGH',
                    'action': f'Address {pattern["category__name"]} Pattern',
                    'description': f'{pattern["count"]} incidents in this category. Needs targeted intervention.',
                    'steps': [
                        f'Discuss {pattern["category__name"]} pattern with student',
                        'Create specific strategies for this area',
                        'Monitor for improvement weekly',
                        'Document progress'
                    ]
                })
            elif pattern['count'] >= 2:
                suggestions.append({
                    'priority': 'MEDIUM',
                    'action': f'Monitor {pattern["category__name"]} Pattern',
                    'description': f'{pattern["count"]} incidents. Watch for escalation.',
                    'steps': [
                        f'Address {pattern["category__name"]} with student',
                        'Track future incidents',
                        'Consider preventive measures'
                    ]
                })
        
        # No intervention yet
        if student.intervention_count == 0 and student.risk_level in ['WARNING', 'CRITICAL']:
            suggestions.append({
                'priority': 'URGENT',
                'action': 'Create Intervention Plan',
                'description': 'No interventions recorded. Plan needed immediately.',
                'steps': [
                    'Schedule student meeting',
                    'Create intervention plan',
                    'Assign counselor or mentor',
                    'Document interventions',
                    'Review progress weekly'
                ]
            })
        
        return JsonResponse({
            'status': 'success',
            'student': {
                'id': student.id,
                'name': student.name,
                'risk_level': student.risk_level,
                'risk_score': student.risk_score,
                'intervention_count': student.intervention_count,
            },
            'suggestions': suggestions,
            'total_suggestions': len(suggestions),
            'category_patterns': list(category_patterns)
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
@user_passes_test(admin_required)
def ai_bulk_risk_update(request):
    """Bulk update risk scores for all students"""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST method required'}, status=405)
    
    try:
        students = Student.objects.filter(is_active=True)
        updated_count = 0
        
        for student in students:
            student.update_risk_score()
            updated_count += 1
        
        # Also update intervention counts
        total_interventions = sum(s.intervention_count for s in students)
        
        return JsonResponse({
            'status': 'success',
            'message': f'Updated {updated_count} students',
            'updated_count': updated_count,
            'total_interventions': total_interventions,
            'timestamp': timezone.now().isoformat(),
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

# ============================================
# SCHOOL SETUP
# ============================================

@login_required
@user_passes_test(admin_required)
def school_setup(request):
    school, _ = School.objects.get_or_create(id=1)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'save_school':
            school.name = request.POST.get('name', 'My School').strip()
            school.short_name = request.POST.get('short_name', '').strip()
            school.motto = request.POST.get('motto', '').strip()
            school.address = request.POST.get('address', '').strip()
            school.phone = request.POST.get('phone', '').strip()
            school.email = request.POST.get('email', '').strip()
            school.website = request.POST.get('website', '').strip()
            school.current_year = int(request.POST.get('current_year', timezone.now().year))
            school.terms_per_year = int(request.POST.get('terms_per_year', 3))
            school.allow_teacher_registration = request.POST.get('allow_teacher_registration') == 'on'
            school.require_teacher_approval = request.POST.get('require_teacher_approval') == 'on'
            school.save()
            messages.success(request, '? School details saved!')
            return redirect('school_setup')

        elif action == 'add_grade':
            try:
                GradeLevel.objects.create(
                    school=school,
                    name=request.POST.get('grade_name', '').strip(),
                    code=request.POST.get('grade_code', '').strip(),
                    order=int(request.POST.get('grade_order', 0)),
                    is_active=True,
                )
                messages.success(request, '? Grade level added!')
            except Exception as e:
                messages.error(request, f'? Error adding grade: {e}')
            return redirect('school_setup')

        elif action == 'delete_grade':
            grade = get_object_or_404(GradeLevel, id=request.POST.get('grade_id'))
            grade_name = grade.name
            student_count = Student.objects.filter(grade_level=grade).count()
            if student_count > 0:
                Student.objects.filter(grade_level=grade).update(grade_level=None)
                messages.warning(
                    request,
                    f'?? {student_count} students had "{grade_name}"  grade removed from their records.'
                )
            grade.delete()
            messages.success(request, f'? Grade "{grade_name}" deleted.')
            return redirect('school_setup')

        elif action == 'add_stream':
            stream_name = request.POST.get('stream_name', '').strip()
            if not stream_name:
                messages.error(request, '? Stream name is required.')
            elif Stream.objects.filter(school=school, name=stream_name).exists():
                messages.error(request, f'? Stream "{stream_name}" already exists!')
            else:
                Stream.objects.create(
                    school=school,
                    name=stream_name,
                    code=request.POST.get('stream_code', stream_name[:3].upper()),
                    is_active=True,
                )
                messages.success(request, f'? Stream "{stream_name}" added!')
            return redirect('school_setup')

        elif action == 'delete_stream':
            stream = get_object_or_404(Stream, id=request.POST.get('stream_id'))
            stream_name = stream.name
            student_count = Student.objects.filter(stream=stream).count()
            if student_count > 0:
                Student.objects.filter(stream=stream).delete()
                messages.warning(
                    request,
                    f'?? {student_count} students in "{stream_name}" were deleted along with the stream.'
                )
            stream.delete()
            messages.success(request, f'? Stream "{stream_name}" deleted.')
            return redirect('school_setup')

        elif action == 'add_term':
            try:
                term = AcademicTerm.objects.create(
                    school=school,
                    name=request.POST.get('term_name', '').strip(),
                    term_number=int(request.POST.get('term_number', 1)),
                    year=int(request.POST.get('term_year', timezone.now().year)),
                    start_date=request.POST.get('start_date'),
                    end_date=request.POST.get('end_date'),
                    is_current=request.POST.get('is_current') == 'on',
                )
                if term.is_current:
                    AcademicTerm.objects.filter(school=school).exclude(id=term.id).update(is_current=False)
                messages.success(request, '? Term added!')
            except Exception as e:
                messages.error(request, f'? Error adding term: {e}')
            return redirect('school_setup')

        elif action == 'delete_term':
            term = get_object_or_404(AcademicTerm, id=request.POST.get('term_id'))
            term.delete()
            messages.success(request, '? Term deleted!')
            return redirect('school_setup')

    unread_notifications = request.user.notifications.filter(is_read=False)

    context = {
        'school': school,
        'grades': GradeLevel.objects.filter(school=school).order_by('order', 'name'),
        'streams': Stream.objects.filter(school=school, is_active=True).order_by('name'),
        'terms': AcademicTerm.objects.filter(school=school).order_by('-year', '-term_number'),
        'notification_count': unread_notifications.count(),
        'unread_notifications': unread_notifications,
    }
    return render(request, 'school_setup.html', context)


# ============================================
# BULK UPLOAD
# ============================================

@login_required
@user_passes_test(admin_required)
def bulk_upload_students(request):
    if request.method == 'POST' and request.FILES.get('excel_file'):
        try:
            excel_file = request.FILES['excel_file']
            if not excel_file.name.lower().endswith(('.xlsx', '.xls')):
                messages.error(request, '? Please upload a valid Excel file (.xlsx or .xls).')
                return redirect('admin_dashboard')

            df = pd.read_excel(excel_file)
            required_columns = ['name', 'admission_number', 'grade', 'stream']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                messages.error(request, f'? Missing required columns: {", ".join(missing_columns)}')
                return redirect('admin_dashboard')

            school = School.objects.first()
            if not school:
                messages.error(request, '? Please set up your school first.')
                return redirect('school_setup')

            added = 0
            errors = []

            for index, row in df.iterrows():
                try:
                    name = str(row['name']).strip()
                    admission = str(row['admission_number']).strip()
                    grade_name = str(row['grade']).strip()
                    stream_name = str(row['stream']).strip()
                    form = str(row.get('form', '')).strip() if pd.notna(row.get('form')) else ''
                    year = int(row['year']) if pd.notna(row.get('year')) else timezone.now().year
                    notes = str(row.get('notes', '')).strip() if pd.notna(row.get('notes')) else ''

                    if not name or not admission:
                        errors.append(f'Row {index + 2}: Missing name or admission number.')
                        continue

                    grade, _ = GradeLevel.objects.get_or_create(
                        school=school,
                        name=grade_name,
                        defaults={'code': grade_name[:3].upper(), 'order': 0, 'is_active': True},
                    )
                    stream, _ = Stream.objects.get_or_create(
                        school=school,
                        name=stream_name,
                        defaults={'code': stream_name[:3].upper(), 'is_active': True},
                    )

                    _, created = Student.objects.get_or_create(
                        admission_number=admission,
                        defaults={
                            'name': name,
                            'stream': stream,
                            'grade_level': grade,
                            'form': form or grade_name,
                            'year': year,
                            'optional_notes': notes,
                            'created_by': request.user,
                            'is_active': True,
                        },
                    )
                    if created:
                        added += 1
                    else:
                        errors.append(f'Row {index + 2}: Student "{admission}" already exists.')
                except Exception as row_error:
                    errors.append(f'Row {index + 2}: {row_error}')

            if added > 0:
                messages.success(request, f'? Successfully added {added} students!')
            if errors:
                for error in errors[:5]:
                    messages.warning(request, f'?? {error}')
                if len(errors) > 5:
                    messages.warning(request, f'?? and {len(errors) - 5} more errors.')

        except Exception as e:
            messages.error(request, f'? Error processing file: {e}')

    return redirect('admin_dashboard')

# Add these to your core/views.py

@login_required
def student_by_admission(request, admission_number):
    """Get student by admission number"""
    try:
        student = get_object_or_404(Student, admission_number=admission_number, is_active=True)
        return JsonResponse({
            'id': student.id,
            'name': student.name,
            'admission_number': student.admission_number,
            'stream': student.stream.name if student.stream else 'N/A',
            'form': student.form,
            'risk_score': student.risk_score,
            'risk_level': student.risk_level,
            'total_reports': student.total_reports,
        })
    except Student.DoesNotExist:
        return JsonResponse({'error': 'Student not found'}, status=404)


@login_required
def mark_all_notifications_read(request):
    """Mark all notifications as read for the current user"""
    if request.method == 'POST':
        notifications = request.user.notifications.filter(is_read=False)
        count = notifications.count()
        
        for notification in notifications:
            notification.mark_read(request.user)
        
        return JsonResponse({
            'status': 'success',
            'message': f'Marked {count} notifications as read',
            'count': count
        })
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)


@login_required
def dashboard_stats_api(request):
    """Get dashboard statistics for API"""
    try:
        students = Student.objects.filter(is_active=True)
        
        stats = {
            'total_students': students.count(),
            'critical_count': students.filter(risk_level='CRITICAL').count(),
            'warning_count': students.filter(risk_level='WARNING').count(),
            'good_count': students.filter(risk_level='GOOD').count(),
            'total_reports': DisciplineReport.objects.count(),
            'reports_today': DisciplineReport.objects.filter(
                reported_at__date=timezone.now().date()
            ).count(),
            'online_teachers': TeacherProfile.objects.filter(is_online=True).count(),
            'pending_approvals': TeacherProfile.objects.filter(
                is_approved=False, is_suspended=False
            ).count(),
            'pending_resets': PasswordReset.objects.filter(status='pending').count(),
        }
        
        return JsonResponse({'status': 'success', 'data': stats})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def get_streams_api(request):
    """Get all streams"""
    try:
        streams = Stream.objects.filter(is_active=True).values('id', 'name', 'code')
        return JsonResponse({
            'status': 'success',
            'streams': list(streams)
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def get_categories_api(request):
    """Get all discipline categories"""
    try:
        categories = DisciplineCategory.objects.filter(
            is_active=True
        ).values('id', 'name', 'key', 'default_rating', 'risk_weight', 'severity_level')
        return JsonResponse({
            'status': 'success',
            'categories': list(categories)
        })
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def export_reports(request, format):
    """Export reports in CSV or Excel format"""
    if format not in ['csv', 'excel']:
        return JsonResponse({'error': 'Invalid format'}, status=400)
    
    try:
        import csv
        from django.http import HttpResponse
        
        reports = DisciplineReport.objects.select_related(
            'student', 'reported_by', 'category'
        ).order_by('-reported_at')
        
        if format == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="reports.csv"'
            
            writer = csv.writer(response)
            writer.writerow([
                'Date', 'Student', 'Admission', 'Category', 
                'Rating', 'Points', 'Reported By', 'Comments'
            ])
            
            for report in reports:
                writer.writerow([
                    report.reported_at.strftime('%Y-%m-%d %H:%M'),
                    report.student.name,
                    report.student.admission_number,
                    report.category_name,
                    report.get_rating_display() if hasattr(report, 'get_rating_display') else dict(DisciplineReport.RATING_CHOICES).get(report.rating, report.rating),
                    report.points,
                    report.reported_by.username,
                    report.comments[:100] + '...' if len(report.comments) > 100 else report.comments,
                ])
            
            return response
        
        else:  # excel
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = 'Reports'
            
            headers = ['Date', 'Student', 'Admission', 'Category', 'Rating', 'Points', 'Reported By', 'Comments']
            ws.append(headers)
            
            for report in reports:
                ws.append([
                    report.reported_at.strftime('%Y-%m-%d %H:%M'),
                    report.student.name,
                    report.student.admission_number,
                    report.category_name,
                    report.get_rating_display() if hasattr(report, 'get_rating_display') else dict(DisciplineReport.RATING_CHOICES).get(report.rating, report.rating),
                    report.points,
                    report.reported_by.username,
                    report.comments[:100] + '...' if len(report.comments) > 100 else report.comments,
                ])
            
            response = HttpResponse(
                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
            response['Content-Disposition'] = 'attachment; filename="reports.xlsx"'
            wb.save(response)
            return response
            
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def export_student_reports(request, student_id):
    """Export reports for a specific student"""
    try:
        student = get_object_or_404(Student, id=student_id, is_active=True)
        reports = student.reports.select_related('reported_by', 'category').order_by('-reported_at')
        
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{student.name}_reports.csv"'
        
        import csv
        writer = csv.writer(response)
        writer.writerow([
            'Date', 'Category', 'Rating', 'Points', 'Reported By', 'Comments'
        ])
        
        for report in reports:
            writer.writerow([
                report.reported_at.strftime('%Y-%m-%d %H:%M'),
                report.category_name,
                report.get_rating_display() if hasattr(report, 'get_rating_display') else dict(DisciplineReport.RATING_CHOICES).get(report.rating, report.rating),
                report.points,
                report.reported_by.username,
                report.comments,
            ])
        
        return response
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
# ============================================
# AI CHAT VIEWS - REAL AI WITH DATABASE ACCESS
# ============================================

@login_required
def ai_chat_page(request):
    """Render the AI Chat page"""
    students = Student.objects.filter(is_active=True).select_related('stream')
    streams = Stream.objects.filter(is_active=True).order_by('name')
    
    context = {
        'students': students[:100],
        'streams': streams,
        'total_students': students.count(),
    }
    return render(request, 'ai_chat.html', context)


@csrf_exempt
@login_required
def ai_chat_api(request):
    """
    API endpoint for AI chat - REAL AI with Pollination API
    ONLY returns AI responses - NO fallback unless API fails
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        student_id = data.get('student_id')
        conversation_history = data.get('history', [])
        use_ai = data.get('use_ai', True)
        
        if not user_message:
            return JsonResponse({'error': 'Please enter a message'}, status=400)
        
        # If use_ai is False, return a simple message
        if not use_ai:
            return JsonResponse({
                'success': True,
                'response': "📋 Fallback mode is enabled. Switch to AI mode for intelligent responses.",
                'mode': 'fallback'
            })
        
        # Try Pollination API
        try:
            from .ai_chat import PollinationAIChat
            ai = PollinationAIChat()
            
            # Get AI response
            result = ai.chat(
                user_message=user_message,
                student_id=student_id,
                conversation_history=conversation_history
            )
            
            if result['success']:
                return JsonResponse({
                    'success': True,
                    'response': result['response'],
                    'usage': result.get('usage', {}),
                    'mode': 'ai'
                })
            else:
                # API failed - return error message
                return JsonResponse({
                    'success': False,
                    'error': result.get('error', 'AI service unavailable. Please try again.'),
                    'mode': 'error'
                }, status=503)
                
        except ImportError:
            return JsonResponse({
                'success': False,
                'error': 'AI service not configured. Please contact administrator.',
                'mode': 'error'
            }, status=500)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'AI service error: {str(e)}',
                'mode': 'error'
            }, status=500)
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def ai_chat_student_context(request):
    """Get student context for AI chat - REAL DATA from database"""
    student_id = request.GET.get('student_id')
    if not student_id:
        return JsonResponse({'error': 'Student ID required'}, status=400)
    
    try:
        student = Student.objects.get(id=student_id, is_active=True)
        reports = student.reports.select_related('category')
        
        data = {
            'id': student.id,
            'name': student.name,
            'admission_number': student.admission_number,
            'stream': student.stream.name if student.stream else 'N/A',
            'form': student.form,
            'risk_score': student.risk_score,
            'risk_level': student.risk_level,
            'total_reports': reports.count(),
            'recent_reports': []
        }
        
        for report in reports.order_by('-reported_at')[:5]:
            data['recent_reports'].append({
                'date': report.reported_at.strftime('%Y-%m-%d %H:%M'),
                'category': report.category.name,
                'rating': report.get_rating_display() if hasattr(report, 'get_rating_display') else dict(DisciplineReport.RATING_CHOICES).get(report.rating, report.rating),
                'points': report.points
            })
        
        return JsonResponse(data)
    except Student.DoesNotExist:
        return JsonResponse({'error': 'Student not found'}, status=404)
    
# ============================================
# ADDITIONAL AI VIEWS - Add to core/views.py
# ============================================

@login_required
def ai_predictive_analysis(request):
    """
    Predict future risk based on current patterns
    Uses historical data to predict which students might be at risk
    """
    try:
        students = Student.objects.filter(is_active=True)
        predictions = []
        
        for student in students:
            reports = student.reports.order_by('reported_at')
            total_reports = reports.count()
            
            # Skip students with no reports
            if total_reports == 0:
                continue
            
            # Calculate report frequency (reports per month)
            if total_reports >= 2:
                first_report = reports.first()
                last_report = reports.last()
                if first_report and last_report:
                    days_diff = (last_report.reported_at - first_report.reported_at).days
                    if days_diff > 0:
                        reports_per_month = (total_reports / days_diff) * 30
                    else:
                        reports_per_month = total_reports
                else:
                    reports_per_month = total_reports
            else:
                reports_per_month = total_reports
            
            # Calculate severity trend
            severity_scores = [r.points for r in reports]
            if len(severity_scores) >= 2:
                severity_trend = severity_scores[-1] - severity_scores[0]
                if severity_trend > 10:
                    trend = 'WORSENING'
                elif severity_trend < -10:
                    trend = 'IMPROVING'
                else:
                    trend = 'STABLE'
            else:
                trend = 'STABLE'
            
            # Calculate days since last incident
            if student.last_incident_date:
                days_since = (timezone.now() - student.last_incident_date).days
                if days_since < 0:
                    days_since = 0
            else:
                days_since = None
            
            # Predict risk level (0-100)
            predicted_risk = student.risk_score
            
            # If reports are frequent and trend is worsening, increase prediction
            if reports_per_month > 2 and trend == 'WORSENING':
                predicted_risk = min(100, predicted_risk + 20)
            elif reports_per_month > 1 and trend == 'WORSENING':
                predicted_risk = min(100, predicted_risk + 10)
            elif trend == 'IMPROVING' and student.intervention_count > 0:
                predicted_risk = max(0, predicted_risk - 10)
            
            # If no recent incidents, reduce prediction
            if days_since and days_since > 30 and student.risk_level == 'WARNING':
                predicted_risk = max(0, predicted_risk - 5)
            
            # Determine risk level from predicted score
            if predicted_risk >= 60:
                predicted_level = 'CRITICAL'
            elif predicted_risk >= 30:
                predicted_level = 'WARNING'
            else:
                predicted_level = 'GOOD'
            
            predictions.append({
                'student_id': student.id,
                'name': student.name,
                'admission_number': student.admission_number,
                'stream': student.stream.name if student.stream else 'N/A',
                'current_risk': student.risk_score,
                'current_level': student.risk_level,
                'predicted_risk': round(predicted_risk, 1),
                'predicted_level': predicted_level,
                'reports_per_month': round(reports_per_month, 2),
                'severity_trend': trend,
                'days_since_incident': days_since,
                'intervention_count': student.intervention_count,
                'needs_attention': predicted_risk >= 60 or (predicted_risk >= 30 and trend == 'WORSENING'),
                'urgency': 'HIGH' if predicted_risk >= 60 else 'MEDIUM' if predicted_risk >= 30 else 'LOW'
            })
        
        # Sort by urgency (HIGH first)
        predictions.sort(key=lambda x: 0 if x['urgency'] == 'HIGH' else 1 if x['urgency'] == 'MEDIUM' else 2)
        
        # Get statistics
        total_predicted = len(predictions)
        high_risk = sum(1 for p in predictions if p['urgency'] == 'HIGH')
        medium_risk = sum(1 for p in predictions if p['urgency'] == 'MEDIUM')
        low_risk = sum(1 for p in predictions if p['urgency'] == 'LOW')
        
        return JsonResponse({
            'status': 'success',
            'data': {
                'predictions': predictions,
                'summary': {
                    'total_analyzed': total_predicted,
                    'high_risk_count': high_risk,
                    'medium_risk_count': medium_risk,
                    'low_risk_count': low_risk,
                    'timestamp': timezone.now().isoformat(),
                }
            }
        })
        
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def ai_behavior_patterns(request):
    """
    Identify behavior patterns across the school
    Analyzes category patterns, time patterns, and student groupings
    """
    try:
        # Get date filters
        days = int(request.GET.get('days', 90))
        start_date = timezone.now() - timedelta(days=days)
        
        reports = DisciplineReport.objects.filter(
            reported_at__gte=start_date
        ).select_related('student', 'category', 'reported_by')
        
        total_reports = reports.count()
        
        if total_reports == 0:
            return JsonResponse({
                'status': 'success',
                'data': {
                    'message': 'No reports in the selected time period',
                    'patterns': []
                }
            })
        
        # Pattern 1: Category Distribution
        category_pattern = reports.values('category__name').annotate(
            count=Count('id')
        ).order_by('-count')
        
        top_categories = list(category_pattern[:5])
        
        # Pattern 2: Time-based patterns (day of week)
        from django.db.models.functions import ExtractWeekDay
        day_patterns = reports.annotate(
            day=ExtractWeekDay('reported_at')
        ).values('day').annotate(
            count=Count('id')
        ).order_by('day')
        
        day_names = {
            1: 'Sunday', 2: 'Monday', 3: 'Tuesday', 4: 'Wednesday',
            5: 'Thursday', 6: 'Friday', 7: 'Saturday'
        }
        
        weekday_patterns = []
        for pattern in day_patterns:
            weekday_patterns.append({
                'day': day_names.get(pattern['day'], 'Unknown'),
                'count': pattern['count'],
                'percentage': round((pattern['count'] / total_reports) * 100, 1)
            })
        
        # Pattern 3: Student grouping patterns
        student_patterns = reports.values('student__name', 'student__stream__name').annotate(
            count=Count('id')
        ).filter(count__gte=3).order_by('-count')[:10]
        
        top_students = []
        for pattern in student_patterns:
            top_students.append({
                'name': pattern['student__name'],
                'stream': pattern['student__stream__name'],
                'report_count': pattern['count']
            })
        
        # Pattern 4: Teacher reporting patterns
        teacher_patterns = reports.values('reported_by__username').annotate(
            count=Count('id')
        ).order_by('-count')[:5]
        
        top_reporters = []
        for pattern in teacher_patterns:
            top_reporters.append({
                'teacher': pattern['reported_by__username'],
                'report_count': pattern['count'],
                'percentage': round((pattern['count'] / total_reports) * 100, 1)
            })
        
        # Pattern 5: Category co-occurrence (which categories often happen together)
        # Get students with multiple reports
        multi_report_students = Student.objects.annotate(
            report_count=Count('reports')
        ).filter(report_count__gte=2)
        
        co_occurrence = []
        for student in multi_report_students[:20]:
            categories = student.reports.values_list('category__name', flat=True).distinct()
            if len(categories) >= 2:
                co_occurrence.append({
                    'student': student.name,
                    'categories': list(categories),
                    'total_reports': student.reports.count()
                })
        
        # Pattern 6: Risk level transitions
        risk_transitions = []
        for student in Student.objects.filter(is_active=True):
            reports_qs = student.reports.order_by('reported_at')
            if reports_qs.count() >= 2:
                first_risk = reports_qs.first().student.risk_level if reports_qs.first().student else 'GOOD'
                last_risk = reports_qs.last().student.risk_level if reports_qs.last().student else 'GOOD'
                if first_risk != last_risk:
                    risk_transitions.append({
                        'student': student.name,
                        'from_risk': first_risk,
                        'to_risk': last_risk,
                        'improved': last_risk == 'GOOD' or (last_risk == 'WARNING' and first_risk == 'CRITICAL')
                    })
        
        # Generate insights
        insights = []
        
        # Category insight
        if top_categories:
            top_cat = top_categories[0]
            if top_cat['count'] > total_reports * 0.3:  # More than 30%
                insights.append({
                    'type': 'category_dominance',
                    'title': f'Dominant Category: {top_cat["category__name"]}',
                    'description': f'{top_cat["count"]} reports ({round((top_cat["count"]/total_reports)*100, 1)}%) in this category',
                    'recommendation': 'Consider targeted interventions for this category'
                })
        
        # Day pattern insight
        if weekday_patterns:
            peak_day = max(weekday_patterns, key=lambda x: x['count'])
            if peak_day['percentage'] > 20:
                insights.append({
                    'type': 'peak_day',
                    'title': f'Peak Day: {peak_day["day"]}',
                    'description': f'{peak_day["count"]} reports ({peak_day["percentage"]}%) occur on {peak_day["day"]}',
                    'recommendation': 'Consider increased monitoring on this day'
                })
        
        # Student pattern insight
        if top_students and top_students[0]['report_count'] > 3:
            insights.append({
                'type': 'frequent_offenders',
                'title': f'Frequent Offenders: {len(top_students)} students with 3+ reports',
                'description': f'Top: {top_students[0]["name"]} ({top_students[0]["report_count"]} reports)',
                'recommendation': 'Implement individual behavior plans for these students'
            })
        
        # Risk transition insight
        worsening = sum(1 for t in risk_transitions if not t['improved'])
        improving = sum(1 for t in risk_transitions if t['improved'])
        if worsening > improving:
            insights.append({
                'type': 'risk_trend',
                'title': f'Risk Trend: Worsening ({worsening} students)',
                'description': f'More students are getting worse than improving ({improving})',
                'recommendation': 'Review school-wide intervention strategies'
            })
        elif improving > worsening:
            insights.append({
                'type': 'risk_trend',
                'title': f'Risk Trend: Improving ({improving} students)',
                'description': f'More students are improving than getting worse ({worsening})',
                'recommendation': 'Continue current effective practices'
            })
        
        return JsonResponse({
            'status': 'success',
            'data': {
                'period_days': days,
                'total_reports': total_reports,
                'category_patterns': top_categories,
                'weekday_patterns': weekday_patterns,
                'top_students': top_students,
                'top_reporters': top_reporters,
                'co_occurrence': co_occurrence[:10],
                'risk_transitions': risk_transitions[:10],
                'insights': insights,
            }
        })
        
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def ai_intervention_effectiveness(request):
    """
    Measure effectiveness of interventions
    Compares student risk before and after interventions
    """
    try:
        # Get students with interventions
        students_with_interventions = Student.objects.filter(
            intervention_count__gt=0,
            is_active=True
        ).select_related('stream')
        
        if not students_with_interventions.exists():
            return JsonResponse({
                'status': 'success',
                'data': {
                    'message': 'No students with interventions recorded',
                    'effectiveness': []
                }
            })
        
        effectiveness_data = []
        improved_count = 0
        worsened_count = 0
        stable_count = 0
        
        for student in students_with_interventions:
            reports = student.reports.order_by('reported_at')
            total_reports = reports.count()
            
            if total_reports < 2:
                # Not enough data to measure
                continue
            
            # Get before and after risk scores
            before_reports = reports[:total_reports // 2]
            after_reports = reports[total_reports // 2:]
            
            before_avg = sum(r.points for r in before_reports) / before_reports.count() if before_reports.count() > 0 else 0
            after_avg = sum(r.points for r in after_reports) / after_reports.count() if after_reports.count() > 0 else 0
            
            # Calculate change
            risk_change = after_avg - before_avg
            
            # Determine effectiveness
            if risk_change < -5:
                status = 'IMPROVED'
                improved_count += 1
                effectiveness = 'High'
            elif risk_change < 0:
                status = 'SLIGHTLY_IMPROVED'
                improved_count += 1
                effectiveness = 'Medium'
            elif risk_change == 0:
                status = 'STABLE'
                stable_count += 1
                effectiveness = 'Moderate'
            elif risk_change < 10:
                status = 'SLIGHTLY_WORSENED'
                worsened_count += 1
                effectiveness = 'Low'
            else:
                status = 'WORSENED'
                worsened_count += 1
                effectiveness = 'Needs Review'
            
            # Get intervention details
            interventions = student.intervention_count
            
            effectiveness_data.append({
                'student_id': student.id,
                'name': student.name,
                'admission_number': student.admission_number,
                'stream': student.stream.name if student.stream else 'N/A',
                'current_risk': student.risk_score,
                'risk_level': student.risk_level,
                'intervention_count': interventions,
                'before_avg_risk': round(before_avg, 1),
                'after_avg_risk': round(after_avg, 1),
                'risk_change': round(risk_change, 1),
                'status': status,
                'effectiveness': effectiveness,
                'total_reports': total_reports,
            })
        
        # Sort by effectiveness
        effectiveness_data.sort(key=lambda x: 0 if x['effectiveness'] == 'High' else 1 if x['effectiveness'] == 'Medium' else 2 if x['effectiveness'] == 'Moderate' else 3)
        
        # Calculate overall statistics
        total_analyzed = len(effectiveness_data)
        total_improved = improved_count
        total_worsened = worsened_count
        total_stable = stable_count
        
        # Calculate effectiveness percentage
        if total_analyzed > 0:
            effectiveness_rate = round((total_improved / total_analyzed) * 100, 1)
        else:
            effectiveness_rate = 0
        
        # Generate recommendations
        recommendations = []
        
        if total_improved > total_worsened:
            recommendations.append({
                'type': 'success',
                'title': f'? Interventions are working ({total_improved} students improved)',
                'description': f'Effectiveness rate: {effectiveness_rate}%',
                'action': 'Continue current intervention strategies'
            })
        elif total_worsened > total_improved:
            recommendations.append({
                'type': 'warning',
                'title': f'?? Interventions need review ({total_worsened} students worsened)',
                'description': f'Only {effectiveness_rate}% of interventions are effective',
                'action': 'Review and revise intervention strategies'
            })
        else:
            recommendations.append({
                'type': 'info',
                'title': f'?? Mixed results ({total_improved} improved, {total_worsened} worsened)',
                'description': f'Effectiveness rate: {effectiveness_rate}%',
                'action': 'Analyze what works and scale successful interventions'
            })
        
        # Add specific student recommendations
        for student_data in effectiveness_data[:5]:
            if student_data['effectiveness'] == 'Needs Review':
                recommendations.append({
                    'type': 'urgent',
                    'title': f'?? Review: {student_data["name"]}',
                    'description': f'Risk increased by {student_data["risk_change"]} points despite {student_data["intervention_count"]} interventions',
                    'action': 'Create new intervention plan and escalate to counselor'
                })
            elif student_data['effectiveness'] == 'Low':
                recommendations.append({
                    'type': 'warning',
                    'title': f'?? Monitor: {student_data["name"]}',
                    'description': f'Risk increased slightly (+{student_data["risk_change"]} points)',
                    'action': 'Review current intervention and adjust strategy'
                })
        
        return JsonResponse({
            'status': 'success',
            'data': {
                'effectiveness_data': effectiveness_data,
                'summary': {
                    'total_analyzed': total_analyzed,
                    'improved_count': total_improved,
                    'worsened_count': total_worsened,
                    'stable_count': total_stable,
                    'effectiveness_rate': effectiveness_rate,
                },
                'recommendations': recommendations,
                'timestamp': timezone.now().isoformat(),
            }
        })
        
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
# ============================================
# USER MANAGEMENT
# ============================================

@login_required
@user_passes_test(admin_required)
def manage_users(request):
    teacher_users = User.objects.filter(
        groups__name__in=['ClassTeacher', 'Teacher']
    ).select_related('teacher_profile').prefetch_related('groups').distinct()

    status_filter = request.GET.get('status', '').strip()
    if status_filter == 'pending':
        teacher_users = teacher_users.filter(
            teacher_profile__is_approved=False,
            teacher_profile__is_suspended=False,
        )
    elif status_filter == 'approved':
        teacher_users = teacher_users.filter(
            teacher_profile__is_approved=True,
            teacher_profile__is_suspended=False,
        )
    elif status_filter == 'suspended':
        teacher_users = teacher_users.filter(teacher_profile__is_suspended=True)

    unread_notifications = request.user.notifications.filter(is_read=False)

    return render(request, 'manage_users.html', {
        'teacher_users': teacher_users,
        'status_filter': status_filter,
        'notification_count': unread_notifications.count(),
        'unread_notifications': unread_notifications,
    })


@login_required
@user_passes_test(admin_required)
def approve_user(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(User, id=user_id)
        profile = get_teacher_profile(user)
        if profile:
            profile.is_approved = True
            profile.approved_at = timezone.now()
            profile.approved_by = request.user
            profile.save()
            notification = Notification.objects.create(
                title='Account Approved',
                message=(
                    f'Your account has been approved by '
                    f'{request.user.get_full_name() or request.user.username}.'
                ),
                notification_type='info',
            )
            notification.target_users.add(user)
            messages.success(request, f'? User {user.username} approved.')
        else:
            messages.error(request, '? Teacher profile not found.')
    return redirect('manage_users')


@login_required
@user_passes_test(admin_required)
def suspend_user(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(User, id=user_id)
        if user.is_superuser:
            messages.error(request, '? Cannot suspend an admin account.')
            return redirect('manage_users')
        reason = request.POST.get('reason', 'No reason provided.').strip()
        profile = get_teacher_profile(user)
        if profile:
            profile.is_suspended = True
            profile.suspension_reason = reason
            profile.suspended_at = timezone.now()
            profile.save()
            messages.success(request, f'? User {user.username} suspended.')
        else:
            messages.error(request, '? Teacher profile not found.')
    return redirect('manage_users')


@login_required
@user_passes_test(admin_required)
def unsuspend_user(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(User, id=user_id)
        profile = get_teacher_profile(user)
        if profile:
            profile.is_suspended = False
            profile.suspension_reason = ''
            profile.suspended_at = None
            profile.save()
            messages.success(request, f'? User {user.username} reinstated.')
        else:
            messages.error(request, '? Teacher profile not found.')
    return redirect('manage_users')


@login_required
@user_passes_test(admin_required)
def delete_user_permanent(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(User, id=user_id)
        if user.is_superuser:
            messages.error(request, '? Cannot delete an admin account.')
            return redirect('manage_users')
        username = user.username
        user.delete()
        messages.success(request, f'? User {username} permanently deleted.')
    return redirect('manage_users')


@login_required
@user_passes_test(admin_required)
def ban_user_permanent(request, user_id):
    if request.method == 'POST':
        user = get_object_or_404(User, id=user_id)
        if user.is_superuser:
            messages.error(request, '? Cannot ban an admin account.')
            return redirect('manage_users')
        reason = request.POST.get('reason', 'Permanent ban').strip()
        profile = get_teacher_profile(user)
        if profile:
            profile.is_suspended = True
            profile.suspension_reason = f'PERMANENT BAN: {reason}'
            profile.suspended_at = timezone.now()
            profile.save()
        user.is_active = False
        user.save()
        messages.success(request, f'? User {user.username} permanently banned.')
    return redirect('manage_users')


# ============================================
# PASSWORD RESET
# ============================================

@csrf_exempt
@never_cache
def request_password_reset(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        if not username:
            messages.error(request, 'Please enter your username.')
            return render(request, 'request_reset.html', {'error': 'Username required'})

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            messages.error(request, 'Username not found.')
            return render(request, 'request_reset.html', {'error': 'Username not found'})

        if PasswordReset.objects.filter(user=user, status='pending').exists():
            messages.warning(request, 'You already have a pending reset request.')
            return render(request, 'request_reset.html', {'warning': 'Pending request exists'})

        reset = PasswordReset.objects.create(user=user, status='pending')

        admins = User.objects.filter(is_superuser=True)
        if admins.exists():
            notification = Notification.objects.create(
                title=f'?? Password Reset Request: {user.username}',
                message=(
                    f'Teacher: {user.get_full_name() or user.username}\n'
                    f'Username: {user.username}\n'
                    f'Requested at: {reset.requested_at.strftime("%Y-%m-%d %H:%M")}'
                ),
                notification_type='warning',
            )
            notification.target_users.set(admins)

        messages.success(request, '? Reset request submitted! Admin will review it.')
        return redirect('login')

    return render(request, 'request_reset.html')


@login_required
@user_passes_test(admin_required)
def admin_reset_requests(request):
    pending_resets = (
        PasswordReset.objects
        .filter(status='pending')
        .select_related('user')
        .order_by('-requested_at')
    )
    unread_notifications = request.user.notifications.filter(is_read=False)
    return render(request, 'admin_reset_requests.html', {
        'pending_resets': pending_resets,
        'notification_count': unread_notifications.count(),
        'unread_notifications': unread_notifications,
    })


@login_required
@user_passes_test(admin_required)
def approve_reset(request, reset_id):
    if request.method == 'POST':
        reset = get_object_or_404(PasswordReset, id=reset_id)
        new_password = get_random_string(10)
        user = reset.user
        user.set_password(new_password)
        user.save()
        reset.status = 'approved'
        reset.resolved_at = timezone.now()
        reset.resolved_by = request.user
        reset.new_password = new_password
        reset.save()
        notification = Notification.objects.create(
            title='? Password Reset Completed',
            message=(
                f'New password: {new_password}\n'
                f'Please login and change your password immediately.'
            ),
            notification_type='info',
        )
        notification.target_users.add(user)
        messages.success(request, f'? Password reset for {user.username}. New password: {new_password}')
    return redirect('admin_reset_requests')


@login_required
@user_passes_test(admin_required)
def reject_reset(request, reset_id):
    if request.method == 'POST':
        reset = get_object_or_404(PasswordReset, id=reset_id)
        reset.status = 'rejected'
        reset.resolved_at = timezone.now()
        reset.resolved_by = request.user
        reset.save()
        messages.success(request, f'? Reset request for {reset.user.username} rejected.')
    return redirect('admin_reset_requests')


# ============================================
# USER PROFILE
# ============================================

@login_required
def user_profile_settings(request):
    user = request.user
    profile = get_teacher_profile(user)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'change_password':
            current_password = request.POST.get('current_password', '')
            new_password = request.POST.get('new_password', '')
            confirm_password = request.POST.get('confirm_password', '')

            if not user.check_password(current_password):
                messages.error(request, 'Current password is incorrect.')
            elif new_password != confirm_password:
                messages.error(request, 'New passwords do not match.')
            elif len(new_password) < 8:
                messages.error(request, 'Password must be at least 8 characters.')
            else:
                user.set_password(new_password)
                user.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Password changed successfully!')
            return redirect('user_profile_settings')

        elif action == 'change_username':
            new_username = request.POST.get('new_username', '').strip()
            current_password = request.POST.get('confirm_password', '')

            if not user.check_password(current_password):
                messages.error(request, 'Password is incorrect.')
            elif not new_username:
                messages.error(request, 'Username cannot be empty.')
            elif User.objects.exclude(id=user.id).filter(username=new_username).exists():
                messages.error(request, 'Username already taken.')
            else:
                user.username = new_username
                user.save()
                messages.success(request, 'Username changed!')
            return redirect('user_profile_settings')

        elif action == 'change_email':
            new_email = request.POST.get('new_email', '').strip()
            current_password = request.POST.get('confirm_password', '')

            if not user.check_password(current_password):
                messages.error(request, 'Password is incorrect.')
            else:
                user.email = new_email
                user.save()
                messages.success(request, 'Email updated!')
            return redirect('user_profile_settings')

        elif action == 'change_full_name':
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            current_password = request.POST.get('confirm_password', '')

            if not user.check_password(current_password):
                messages.error(request, 'Password is incorrect.')
            else:
                user.first_name = first_name
                user.last_name = last_name
                user.save()
                messages.success(request, 'Name updated!')
            return redirect('user_profile_settings')

        # ✅ FIXED: Profile picture upload (works with Supabase Storage)
        elif action == 'change_profile_picture':
            if request.FILES.get('profile_picture'):
                try:
                    if not profile:
                        profile = TeacherProfile.objects.create(user=user)
                    
                    # ✅ Just save the new picture - Supabase handles the old file
                    profile.profile_picture = request.FILES['profile_picture']
                    profile.save()
                    messages.success(request, '✅ Profile picture updated successfully!')
                except Exception as e:
                    messages.error(request, f'❌ Error uploading picture: {e}')
            else:
                messages.error(request, 'Please select an image to upload.')
            return redirect('user_profile_settings')

    unread_notifications = user.notifications.filter(is_read=False)

    context = {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'profile_user': user,
        'profile': profile,
        'notification_count': unread_notifications.count(),
        'unread_notifications': unread_notifications,
    }
    return render(request, 'user_profile.html', context)


@login_required
def admin_profile(request):
    return redirect('user_profile_settings')


# ✅ FIXED: Upload profile picture (works with Supabase Storage)
@login_required
def upload_profile_picture(request):
    if request.method == 'POST' and request.FILES.get('profile_picture'):
        try:
            profile = get_teacher_profile(request.user)
            if not profile:
                profile = TeacherProfile.objects.create(user=request.user)

            # ✅ Just save the new picture - Supabase handles everything
            profile.profile_picture = request.FILES['profile_picture']
            profile.save()
            messages.success(request, '✅ Profile picture updated successfully!')
        except Exception as e:
            messages.error(request, f'❌ Error uploading picture: {e}')
    else:
        messages.error(request, '❌ No image file provided.')
    return redirect('user_profile_settings')


@login_required
def view_user_profile(request, user_id):
    is_teacher = request.user.groups.filter(name__in=['Teacher', 'ClassTeacher']).exists()
    if not (request.user.is_superuser or is_teacher or request.user.id == user_id):
        messages.error(request, '❌ You do not have permission to view this profile.')
        return redirect('user_profile_settings')

    try:
        viewed_user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, '❌ User not found.')
        return redirect('user_profile_settings')

    viewed_profile = get_teacher_profile(viewed_user)
    unread_notifications = request.user.notifications.filter(is_read=False)

    context = {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'profile_user': viewed_user,
        'profile': viewed_profile,
        'notification_count': unread_notifications.count(),
        'unread_notifications': unread_notifications,
    }
    return render(request, 'user_profile.html', context)


# ============================================
# STUDENT EDIT
# ============================================

@login_required
def edit_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)

    is_allowed = request.user.is_superuser
    if not is_allowed:
        profile = get_teacher_profile(request.user)
        if profile and profile.assigned_stream == student.stream:
            is_allowed = True

    if not is_allowed:
        messages.error(request, '❌ Permission denied.')
        return redirect('student_profile', student_id=student_id)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_student':
            try:
                student.name = request.POST.get('name', '').strip()
                student.form = request.POST.get('form', '')
                student.year = int(request.POST.get('year', timezone.now().year))
                student.optional_notes = request.POST.get('optional_notes', '')

                # ✅ FIXED: Student picture upload (works with Supabase Storage)
                if request.FILES.get('profile_picture'):
                    student.profile_picture = request.FILES['profile_picture']

                student.save()
                messages.success(request, '✅ Student updated successfully!')
            except Exception as e:
                messages.error(request, f'❌ Error updating student: {e}')
            return redirect('student_profile', student_id=student.id)

        elif action == 'delete_student':
            student_name = student.name
            if request.user.is_superuser:
                student.delete()
                messages.success(request, f'✅ Student {student_name} permanently deleted.')
            else:
                student.is_active = False
                student.save()
                messages.success(request, f'✅ Student {student_name} deactivated.')
            return redirect('class_teacher_dashboard')

    return render(request, 'edit_student.html', {
        'student': student,
        'forms': _get_form_choices(),
    })

# ============================================
# ADMIN NOTIFICATIONS
# ============================================

@login_required
@user_passes_test(admin_required)
def get_admin_notifications(request):
    notifications = []

    pending_approvals = User.objects.filter(
        teacher_profile__is_approved=False,
        teacher_profile__is_suspended=False,
        groups__name='ClassTeacher',
    ).exclude(id=request.user.id).count()
    if pending_approvals > 0:
        notifications.append({
            'type': 'approval',
            'title': f'{pending_approvals} Pending Approvals',
            'count': pending_approvals,
            'url': '/manage-users/?status=pending',
        })

    pending_resets = PasswordReset.objects.filter(status='pending').count()
    if pending_resets > 0:
        notifications.append({
            'type': 'reset',
            'title': f'{pending_resets} Password Reset Requests',
            'count': pending_resets,
            'url': '/admin-reset-requests/',
        })

    high_report_count = Student.objects.filter(is_active=True).annotate(
        report_count=Count('reports')
    ).filter(report_count__gte=20).count()
    if high_report_count > 0:
        notifications.append({
            'type': 'critical',
            'title': f'{high_report_count} Students with 20+ Reports',
            'count': high_report_count,
            'url': '/admin-dashboard/',
        })

    reports_today = DisciplineReport.objects.filter(
        reported_at__date=timezone.now().date()
    ).count()
    if reports_today > 0:
        notifications.append({
            'type': 'info',
            'title': f'{reports_today} New Reports Today',
            'count': reports_today,
            'url': '/admin-dashboard/',
        })

    return JsonResponse({'notifications': notifications, 'total': len(notifications)})


# ============================================
# ERROR REPORTING
# ============================================

@csrf_exempt
@login_required
def report_error(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            print('\n' + '=' * 80)
            print('?? ERROR REPORT RECEIVED')
            print('=' * 80)
            print(f"? Time:  {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"?? URL:   {data.get('url', 'Unknown')}")
            print(f"?? User:  {request.user.username}")
            print('-' * 80)
            print('?? Error Details:')
            print(data.get('error', 'No details provided'))
            print('=' * 80 + '\n')
            return JsonResponse({'status': 'success', 'message': 'Error reported.'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error', 'message': 'Method not allowed.'}, status=405)


# ============================================
# DEBUG / TEST VIEWS  (admin-only, dev helpers)
# ============================================

@login_required
@user_passes_test(admin_required)
def debug_streams(request):
    """Returns a JSON snapshot of streams, grades, and school  useful for debugging."""
    streams = list(Stream.objects.values('id', 'name', 'is_active'))
    grades = list(GradeLevel.objects.values('id', 'name', 'is_active'))
    school = School.objects.values('id', 'name').first()
    return JsonResponse({
        'school': school,
        'streams': streams,
        'grades': grades,
        'total_streams': len(streams),
        'total_grades': len(grades),
    })


@login_required
@user_passes_test(admin_required)
def test_streams(request):
    """Renders test_streams.html with stream and grade data for template debugging."""
    unread_notifications = request.user.notifications.filter(is_read=False)
    context = {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'grades': GradeLevel.objects.filter(is_active=True).order_by('order', 'name'),
        'notification_count': unread_notifications.count(),
        'unread_notifications': unread_notifications,
    }
    return render(request, 'test_streams.html', context)


def media_test(request):
    """Test if media files are accessible"""
    import os
    from django.conf import settings
    from django.http import JsonResponse
    
    media_path = settings.MEDIA_ROOT
    files = []
    if os.path.exists(media_path):
        for root, dirs, filenames in os.walk(media_path):
            for filename in filenames:
                files.append(os.path.join(root, filename))
    
    return JsonResponse({
        'media_root': str(media_path),
        'exists': os.path.exists(media_path),
        'files': files[:20],
        'media_url': settings.MEDIA_URL,
        'debug': settings.DEBUG,
        'message': 'Media serving is working!'
    })


# ============================================
# ATTENDANCE VIEWS
# ============================================

@login_required
def attendance_list(request):
    stream_filter = request.GET.get('stream')
    form_filter = request.GET.get('form')
    date_filter = request.GET.get('date', timezone.now().date().isoformat())
    
    attendance_qs = AttendanceRecord.objects.all()
    if stream_filter:
        attendance_qs = attendance_qs.filter(stream_id=stream_filter)
    if form_filter:
        attendance_qs = attendance_qs.filter(form=form_filter)
    attendance_qs = attendance_qs.filter(date=date_filter).select_related('student__stream')
    
    stats = {
        'total': attendance_qs.count(),
        'present': attendance_qs.filter(status='PRESENT').count(),
        'absent': attendance_qs.filter(status='ABSENT').count(),
        'late': attendance_qs.filter(status='LATE').count(),
        'excused': attendance_qs.filter(status='EXCUSED').count(),
        'attendance_rate': 0,
    }
    if stats['total'] > 0:
        stats['attendance_rate'] = round(stats['present'] / stats['total'] * 100, 1)
    
    context = {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'forms': _get_form_choices(),
        'attendance_records': attendance_qs.order_by('student__name'),
        'stats': stats,
        'selected_date': date_filter,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
        'unread_notifications': request.user.notifications.filter(is_read=False).order_by('-created_at')[:20],
    }
    return render(request, 'attendance_list.html', context)


@csrf_exempt
@login_required
def api_qr_checkin(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=405)
    try:
        data = json.loads(request.body)
        qr_code = data.get('qr_code', '').strip()
        stream_id = data.get('stream_id')
        
        if not qr_code or not stream_id:
            return JsonResponse({'success': False, 'error': 'qr_code and stream_id required'}, status=400)
        
        student = Student.objects.filter(admission_number=qr_code, is_active=True).first()
        if not student:
            return JsonResponse({'success': False, 'error': 'Invalid QR code'}, status=404)
        
        stream = Stream.objects.get(id=stream_id, is_active=True)
        today = timezone.now().date()
        
        attendance, created = AttendanceRecord.objects.update_or_create(
            student=student, date=today, stream=stream, defaults={
                'form': student.form,
                'status': 'PRESENT',
                'checkin_method': 'QR_CODE',
                'qr_code_data': qr_code,
                'check_in_time': timezone.now(),
                'marked_by': request.user,
            }
        )
        
        return JsonResponse({
            'success': True,
            'student_name': student.name,
            'admission_number': student.admission_number,
            'status': attendance.status,
            'created': created,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================
# GAMIFICATION VIEWS
# ============================================

@login_required
def gamification_dashboard(request):
    students = Student.objects.filter(is_active=True).select_related('stream')
    
    top_students = []
    for student in students:
        total_points = sum(p.points for p in student.points_history.all())
        top_students.append({
            'student': student,
            'total_points': total_points,
            'rewards_count': student.rewards_earned.count(),
        })
    top_students.sort(key=lambda x: x['total_points'], reverse=True)
    
    school = School.objects.first()
    rewards = Reward.objects.filter(school=school, is_active=True) if school else Reward.objects.none()
    
    recent_awards = StudentReward.objects.select_related('student', 'reward').order_by('-created_at')[:20]
    
    context = {
        'top_students': top_students[:20],
        'rewards': rewards,
        'recent_awards': recent_awards,
        'total_points_awarded': StudentPoints.objects.aggregate(total=Sum('points'))['total'] or 0,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
        'unread_notifications': request.user.notifications.filter(is_read=False).order_by('-created_at')[:20],
    }
    return render(request, 'gamification_dashboard.html', context)


@login_required
def award_points(request, student_id):
    if request.method == 'POST':
        student = get_object_or_404(Student, id=student_id, is_active=True)
        points = int(request.POST.get('points', 0))
        reason = request.POST.get('reason', 'MANUAL_ADJUSTMENT')
        description = request.POST.get('description', '')
        
        StudentPoints.objects.create(
            student=student,
            points=points,
            reason=reason,
            description=description,
            awarded_by=request.user,
        )
        
        from .automation import RewardAutomation
        RewardAutomation.check_and_award_rewards(student_id)
        
        messages.success(request, f'Awarded {points:+.0f} points to {student.name}')
    return redirect('gamification_dashboard')


@login_required
def award_reward(request, student_id):
    if request.method == 'POST':
        student = get_object_or_404(Student, id=student_id, is_active=True)
        reward_id = request.POST.get('reward_id')
        notes = request.POST.get('notes', '')
        
        reward = get_object_or_404(Reward, id=reward_id, is_active=True)
        StudentReward.objects.create(
            student=student,
            reward=reward,
            awarded_by=request.user,
            notes=notes,
        )
        
        from .notifications import NotificationOrchestrator
        NotificationOrchestrator().notify_reward_earned(student, reward)
        
        messages.success(request, f'{student.name} earned the "{reward.name}" reward!')
    return redirect('gamification_dashboard')


# ============================================
# TIMETABLE VIEWS
# ============================================

@login_required
def timetable_view(request):
    stream_filter = request.GET.get('stream')
    day_filter = request.GET.get('day')
    
    entries = TimetableEntry.objects.filter(is_active=True)
    if stream_filter:
        entries = entries.filter(stream_id=stream_filter)
    if day_filter:
        entries = entries.filter(day=day_filter)
    
    days = ['MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY']
    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    
    context = {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'entries': entries.select_related('subject', 'teacher', 'stream').order_by('day', 'start_time'),
        'days': list(zip(days, day_names)),
        'selected_day': day_filter,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
        'unread_notifications': request.user.notifications.filter(is_read=False).order_by('-created_at')[:20],
    }
    return render(request, 'timetable.html', context)


@login_required
def create_timetable(request):
    if request.method == 'POST':
        stream_id = request.POST.get('stream')
        subject_id = request.POST.get('subject')
        form = request.POST.get('form')
        day = request.POST.get('day')
        start_time = request.POST.get('start_time')
        end_time = request.POST.get('end_time')
        room = request.POST.get('room', '')
        teacher_id = request.POST.get('teacher')
        
        try:
            stream = Stream.objects.get(id=stream_id, is_active=True)
            subject = Subject.objects.get(id=subject_id)
            teacher = User.objects.get(id=teacher_id) if teacher_id else None
            
            entry = TimetableEntry.objects.create(
                school=stream.school,
                stream=stream,
                form=form,
                subject=subject,
                teacher=teacher,
                day=day,
                start_time=start_time,
                end_time=end_time,
                room=room,
            )
            messages.success(request, f'Timetable entry created: {entry}')
        except Exception as e:
            messages.error(request, f'Error creating timetable entry: {e}')
        return redirect('timetable')
    
    context = {
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'subjects': Subject.objects.filter(is_active=True).order_by('name'),
        'teachers': TeacherProfile.objects.filter(is_approved=True).select_related('user').order_by('user__username'),
        'days': TimetableEntry.DAY_CHOICES,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
    }
    return render(request, 'timetable_create.html', context)


# ============================================
# DOCUMENT VAULT VIEWS
# ============================================

@login_required
def document_vault(request, student_id):
    student = get_object_or_404(Student, id=student_id, is_active=True)
    
    if request.method == 'POST' and request.FILES.get('file'):
        try:
            doc = DocumentVault.objects.create(
                student=student,
                document_type=request.POST.get('document_type', 'OTHER'),
                title=request.POST.get('title', 'Untitled'),
                description=request.POST.get('description', ''),
                file=request.FILES['file'],
                is_confidential=request.POST.get('is_confidential') == 'on',
                uploaded_by=request.user,
            )
            messages.success(request, f'Document "{doc.title}" uploaded successfully!')
        except Exception as e:
            messages.error(request, f'Error uploading document: {e}')
        return redirect('document_vault', student_id=student_id)
    
    documents = student.documents.order_by('-created_at')
    
    context = {
        'student': student,
        'documents': documents,
        'doc_types': DocumentVault.DOCUMENT_TYPE_CHOICES,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
        'unread_notifications': request.user.notifications.filter(is_read=False).order_by('-created_at')[:20],
    }
    return render(request, 'document_vault.html', context)


@login_required
def delete_document(request, doc_id):
    doc = get_object_or_404(DocumentVault, id=doc_id)
    student_id = doc.student.id
    try:
        doc.file.delete()
        doc.delete()
        messages.success(request, 'Document deleted successfully!')
    except Exception as e:
        messages.error(request, f'Error deleting document: {e}')
    return redirect('document_vault', student_id=student_id)


# ============================================
# PARENT PORTAL VIEWS
# ============================================

@login_required
def parent_portal(request):
    try:
        parent = request.user.parent_profile
        links = StudentParentLink.objects.filter(parent=parent).select_related('student__stream')
        
        children_data = []
        for link in links:
            student = link.student
            reports = student.reports.order_by('-reported_at')[:5]
            attendance = student.attendance_records.order_by('-date')[:10]
            
            children_data.append({
                'link': link,
                'student': student,
                'recent_reports': reports,
                'recent_attendance': attendance,
                'total_reports': student.total_reports,
                'risk_score': student.risk_score,
                'risk_level': student.risk_level,
                'total_points': sum(p.points for p in student.points_history.all()),
                'rewards': student.rewards_earned.select_related('reward').order_by('-created_at')[:5],
            })
        
        context = {
            'children_data': children_data,
            'notification_count': request.user.notifications.filter(is_read=False).count(),
            'unread_notifications': request.user.notifications.filter(is_read=False).order_by('-created_at')[:20],
        }
        return render(request, 'parent_portal.html', context)
    except ParentProfile.DoesNotExist:
        messages.error(request, 'Parent profile not found. Contact administrator.')
        return redirect('dashboard')


@login_required
def parent_student_detail(request, student_id):
    try:
        parent = request.user.parent_profile
        link = StudentParentLink.objects.get(parent=parent, student_id=student_id)
        student = link.student
        
        reports = student.reports.select_related('category', 'reported_by').order_by('-reported_at')
        points = student.points_history.order_by('-created_at')
        attendance = student.attendance_records.order_by('-date')
        rewards = student.rewards_earned.select_related('reward').order_by('-created_at')
        
        context = {
            'student': student,
            'link': link,
            'reports': reports[:20],
            'points': points[:20],
            'attendance': attendance[:30],
            'rewards': rewards[:10],
            'total_points': sum(p.points for p in points),
            'notification_count': request.user.notifications.filter(is_read=False).count(),
        }
        return render(request, 'parent_student_detail.html', context)
    except (ParentProfile.DoesNotExist, StudentParentLink.DoesNotExist):
        messages.error(request, 'Access denied.')
        return redirect('dashboard')


# ============================================
# AI AETHER VIEWS
# ============================================

@login_required
def aether_dashboard(request):
    students = Student.objects.filter(is_active=True)
    total_students = students.count()
    critical = students.filter(risk_level='CRITICAL').count()
    warning = students.filter(risk_level='WARNING').count()
    good = students.filter(risk_level='GOOD').count()
    
    recent_reports = DisciplineReport.objects.select_related('student', 'category').order_by('-reported_at')[:10]
    
    context = {
        'total_students': total_students,
        'critical_count': critical,
        'warning_count': warning,
        'good_count': good,
        'recent_reports': recent_reports,
        'categories': DisciplineCategory.objects.filter(is_active=True).order_by('order', 'name'),
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'notification_count': request.user.notifications.filter(is_read=False).count(),
        'unread_notifications': request.user.notifications.filter(is_read=False).order_by('-created_at')[:20],
    }
    return render(request, 'aether_dashboard.html', context)


@login_required
def aether_chat_api(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        student_id = data.get('student_id')
        conversation_history = data.get('history', [])
        
        if not user_message:
            return JsonResponse({'error': 'Please enter a message'}, status=400)
        
        from .aether_integration import AetherAI
        ai = AetherAI()
        result = ai.chat(user_message, student_id=student_id, conversation_history=conversation_history)
        
        if result['success']:
            return JsonResponse({
                'success': True,
                'response': result['response'],
                'usage': result.get('usage', {}),
                'mode': 'ai',
            })
        else:
            return JsonResponse({
                'success': False,
                'error': result.get('error', 'AI service unavailable'),
                'mode': 'error',
            }, status=503)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================
# ANALYTICS VIEWS
# ============================================

@login_required
def analytics_dashboard(request):
    school = School.objects.first()
    students = Student.objects.filter(is_active=True)
    total = students.count()
    critical = students.filter(risk_level='CRITICAL').count()
    warning = students.filter(risk_level='WARNING').count()
    good = students.filter(risk_level='GOOD').count()
    
    context = {
        'school': school,
        'total_students': total,
        'critical_count': critical,
        'warning_count': warning,
        'good_count': good,
        'good_percentage': round(good / total * 100, 1) if total else 0,
        'warning_percentage': round(warning / total * 100, 1) if total else 0,
        'critical_percentage': round(critical / total * 100, 1) if total else 0,
        'total_reports': DisciplineReport.objects.count(),
        'reports_today': DisciplineReport.objects.filter(reported_at__date=timezone.now().date()).count(),
        'streams': Stream.objects.filter(is_active=True).order_by('name'),
        'categories': DisciplineCategory.objects.filter(is_active=True).order_by('order', 'name'),
        'notification_count': request.user.notifications.filter(is_read=False).count(),
        'unread_notifications': request.user.notifications.filter(is_read=False).order_by('-created_at')[:20],
    }
    return render(request, 'analytics_dashboard.html', context)


# ============================================
# MOBILE VIEWS
# ============================================

@login_required
def mobile_dashboard(request):
    students = Student.objects.filter(is_active=True).select_related('stream')
    total = students.count()
    critical = students.filter(risk_level='CRITICAL').count()
    warning = students.filter(risk_level='WARNING').count()
    good = students.filter(risk_level='GOOD').count()
    
    recent_reports = DisciplineReport.objects.select_related('student', 'category').order_by('-reported_at')[:15]
    
    context = {
        'total_students': total,
        'critical_count': critical,
        'warning_count': warning,
        'good_count': good,
        'recent_reports': recent_reports,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
    }
    return render(request, 'mobile_dashboard.html', context)


# ============================================
# SCHOOL MANAGEMENT VIEWS
# ============================================

@login_required
@user_passes_test(admin_required)
def manage_subjects(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'add_subject':
            try:
                school = School.objects.first()
                Subject.objects.create(
                    school=school,
                    name=request.POST.get('name', '').strip(),
                    code=request.POST.get('code', '').strip(),
                    description=request.POST.get('description', ''),
                )
                messages.success(request, 'Subject added!')
            except Exception as e:
                messages.error(request, f'Error: {e}')
        elif action == 'delete_subject':
            subject = get_object_or_404(Subject, id=request.POST.get('subject_id'))
            subject.delete()
            messages.success(request, 'Subject deleted!')
        return redirect('manage_subjects')
    
    subjects = Subject.objects.all().order_by('name')
    context = {
        'subjects': subjects,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
    }
    return render(request, 'manage_subjects.html', context)


# ============================================
# API TEST VIEWS
# ============================================

@login_required
def api_test_page(request):
    return render(request, 'api_test.html', {
        'notification_count': request.user.notifications.filter(is_read=False).count(),
    })


# ============================================
# WORKFLOW: APPROVALS, RESOLUTION, SANCTIONS, APPEALS, COMMS
# ============================================

def _can_review_reports(user):
    """Admins and Class Teachers perform second sign-off."""
    return user.is_superuser or user.groups.filter(name='ClassTeacher').exists()


def _log_report_action(report, action, user, note=''):
    ReportHistory.objects.create(
        report=report, action=action, changed_by=user, note=note,
        snapshot={
            'approval_status': report.approval_status,
            'is_resolved': report.is_resolved,
            'points': report.points,
        },
    )


@login_required
@user_passes_test(lambda u: _can_review_reports(u))
def review_report(request, report_id):
    """Approve or reject a report requiring second sign-off."""
    report = get_object_or_404(DisciplineReport.objects.select_related('student'), id=report_id)
    if request.method == 'POST':
        decision = request.POST.get('decision', '').upper()
        notes = request.POST.get('review_notes', '')
        if decision == 'APPROVE':
            report.approve(request.user, notes)
            _log_report_action(report, 'APPROVE', request.user, notes)
            messages.success(request, f'? Report for {report.student.name} approved.')
        elif decision == 'REJECT':
            report.reject(request.user, notes)
            _log_report_action(report, 'REJECT', request.user, notes)
            messages.warning(request, f'Report for {report.student.name} rejected.')
        else:
            messages.error(request, 'Invalid decision.')
        return redirect(request.POST.get('next', 'student_profile'), report.student_id)
    return render(request, 'includes/report_review.html', {
        'report': report,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
@user_passes_test(lambda u: _can_review_reports(u))
def resolve_report(request, report_id):
    """Mark a report resolved with an intervention note."""
    report = get_object_or_404(DisciplineReport, id=report_id)
    if request.method == 'POST':
        notes = request.POST.get('intervention_notes', '')
        report.resolve(request.user, notes)
        _log_report_action(report, 'RESOLVE', request.user, notes)
        messages.success(request, f'? {report.student.name}\'s case marked resolved.')
        return redirect(request.POST.get('next', 'student_profile'), report.student_id)
    return redirect('student_profile', report.student_id)


@login_required
@user_passes_test(lambda u: _can_review_reports(u))
def add_sanction(request, report_id):
    """Attach a sanction (detention, counseling, etc.) to a report."""
    report = get_object_or_404(DisciplineReport.select_related('student'), id=report_id)
    if request.method == 'POST':
        s_type = request.POST.get('sanction_type')
        valid = [c[0] for c in Sanction.TYPE_CHOICES]
        if s_type not in valid:
            messages.error(request, 'Invalid sanction type.')
            return redirect(request.POST.get('next', 'student_profile'), report.student_id)
        Sanction.objects.create(
            report=report,
            student=report.student,
            sanction_type=s_type,
            description=request.POST.get('description', ''),
            scheduled_date=request.POST.get('scheduled_date') or None,
            assigned_to=get_object_or_404(User, id=request.POST['assigned_to']) if request.POST.get('assigned_to') else None,
            created_by=request.user,
        )
        messages.success(request, f'⚖️ Sanction added for {report.student.name}.')
        return redirect(request.POST.get('next', 'student_profile'), report.student_id)
    return redirect('student_profile', report.student_id)


@login_required
@user_passes_test(lambda u: _can_review_reports(u))
def update_sanction(request, sanction_id):
    """Update a sanction's status / completion."""
    sanction = get_object_or_404(Sanction, id=sanction_id)
    if request.method == 'POST':
        status = request.POST.get('status')
        valid = [c[0] for c in Sanction.STATUS_CHOICES]
        if status in valid:
            sanction.status = status
            if status == 'COMPLETED':
                sanction.completed_date = timezone.now().date()
            sanction.save(update_fields=['status', 'completed_date', 'updated_at'])
            messages.success(request, 'Sanction updated.')
        return redirect(request.POST.get('next', 'student_profile'), sanction.student_id)
    return redirect('student_profile', sanction.student_id)


@login_required
def file_appeal(request, student_id):
    """Student or parent files a dispute against a report."""
    student = get_object_or_404(Student, id=student_id)
    # Access: teacher/staff can appeal on behalf; parents only for their linked children
    is_staff = _can_review_reports(request.user)
    is_linked_parent = StudentParentLink.objects.filter(
        student=student, parent__user=request.user, receive_notifications=True
    ).exists()
    if not (is_staff or is_linked_parent):
        messages.error(request, 'You are not authorized to appeal for this student.')
        return redirect('student_profile', student_id)
    if request.method == 'POST':
        report_id = request.POST.get('report_id')
        report = get_object_or_404(DisciplineReport, id=report_id, student=student)
        Appeal.objects.create(
            report=report,
            student=student,
            filed_by=request.user,
            filed_by_role='PARENT' if is_linked_parent and not is_staff else 'STAFF',
            reason=request.POST.get('reason', ''),
        )
        messages.success(request, '? Appeal filed. The case will be reviewed by a senior staff member.')
        return redirect('student_profile', student_id)
    reports = DisciplineReport.objects.filter(student=student, is_deleted=False).order_by('-reported_at')
    return render(request, 'includes/file_appeal.html', {
        'student': student,
        'reports': reports,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
@user_passes_test(lambda u: _can_review_reports(u))
def review_appeal(request, appeal_id):
    """Senior staff upholds or overturns an appeal."""
    appeal = get_object_or_404(Appeal, id=appeal_id)
    if request.method == 'POST':
        status = request.POST.get('status')
        valid = [c[0] for c in Appeal.STATUS_CHOICES]
        if status in valid:
            appeal.review(request.user, status, request.POST.get('review_notes', ''))
            messages.success(request, f'Appeal {status.lower()}.')
        return redirect(request.POST.get('next', 'student_profile'), appeal.student_id)
    return redirect('student_profile', appeal.student_id)


@login_required
@user_passes_test(lambda u: _can_review_reports(u))
def communicate_parent(request, student_id):
    """Send a consented communication to the student's parent/guardian."""
    student = get_object_or_404(Student, id=student_id)
    if request.method == 'POST':
        channel = request.POST.get('channel', 'EMAIL')
        parent_link = StudentParentLink.objects.filter(
            student=student, receive_notifications=True
        ).select_related('parent__user').first()
        if not parent_link:
            messages.warning(request, 'No parent/guardian linked for this student.')
            return redirect('student_profile', student_id)
        parent = parent_link.parent
        consent = {
            'EMAIL': parent.receive_email,
            'SMS': parent.receive_sms,
            'PUSH': parent.receive_push,
        }.get(channel, False)
        report_id = request.POST.get('report_id')
        report = DisciplineReport.objects.filter(id=report_id, student=student).first() if report_id else None
        log = CommunicationLog.objects.create(
            student=student,
            parent=parent,
            channel=channel,
            subject=request.POST.get('subject', 'Discipline Update'),
            body=request.POST.get('body', ''),
            report=report,
            sent_by=request.user,
            consent_given=consent,
            status='SENT' if consent else 'FAILED',
        )
        if consent:
            create_user_notification(
                parent.user,
                f'? Update: {student.name}',
                log.body,
                'info', student
            )
            messages.success(request, f'? Message sent to parent ({channel}).')
        else:
            messages.warning(request, f'Parent has not consented to {channel}; communication logged only.')
        return redirect('student_profile', student_id)
    links = StudentParentLink.objects.filter(student=student).select_related('parent__user')
    return render(request, 'includes/communicate_parent.html', {
        'student': student,
        'links': links,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
def student_history(request, student_id):
    """Full audit timeline for a student (reports + approvals + sanctions + appeals)."""
    student = get_object_or_404(Student, id=student_id)
    history = ReportHistory.objects.filter(report__student=student).select_related('report', 'changed_by').order_by('-changed_at')
    return render(request, 'includes/student_history.html', {
        'student': student,
        'history': history,
        'notification_count': request.user.notifications.filter(is_read=False).count(),
    })


@login_required
def report_history_api(request, report_id):
    """JSON audit history for a single report (used by modals)."""
    report = get_object_or_404(DisciplineReport, id=report_id)
    data = [{
        'action': h.action,
        'changed_by': h.changed_by.get_full_name() if h.changed_by else 'System',
        'changed_at': h.changed_at.isoformat(),
        'note': h.note,
    } for h in report.history.select_related('changed_by').order_by('-changed_at')]
    return JsonResponse({'history': data})