"""
REST API Module
Full REST API for mobile app integration.
All endpoints return JSON responses.
"""
import json
import os
import logging
from datetime import datetime, timedelta, date as dt_date
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import authenticate
from django.contrib.auth.models import User, Group
from django.db.models import Count, Sum, Avg, Q, F, Max, Min
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.files.storage import default_storage
from django.core.paginator import Paginator

from .models import (
    Student, DisciplineReport, DisciplineCategory, Notification,
    Stream, TeacherProfile, UserSession, School, GradeLevel,
    AcademicTerm, Subject, TimetableEntry, AttendanceRecord,
    ParentProfile, StudentParentLink, StudentPoints, Reward,
    StudentReward, LeaderboardEntry, DocumentVault, PasswordReset,
)

logger = logging.getLogger(__name__)


def require_api_auth(view_func):
    """Gate a REST endpoint: allow session auth OR a valid JWT Bearer token.

    Returns 401 JSON for unauthenticated requests instead of leaking data.
    """
    from functools import wraps
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework.exceptions import AuthenticationFailed

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if request.user.is_authenticated:
            return view_func(request, *args, **kwargs)
        auth = request.META.get('HTTP_AUTHORIZATION', '')
        if auth.startswith('Bearer '):
            try:
                validated = JWTAuthentication().authenticate(
                    type('R', (), {'META': {'HTTP_AUTHORIZATION': auth}})()
                )
                if validated:
                    request.user = validated[0]
                    return view_func(request, *args, **kwargs)
            except AuthenticationFailed:
                pass
        return JsonResponse({'error': 'Authentication required'}, status=401)

    return _wrapped


# ============================================
# AUTHENTICATION API
# ============================================

@csrf_exempt
def api_login(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=405)
    try:
        data = json.loads(request.body)
        username = data.get('username', '').strip()
        password = data.get('password', '')
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            if not user.is_active:
                return JsonResponse({'error': 'Account permanently banned'}, status=403)
            
            from django.contrib.auth import login
            login(request, user)
            
            return JsonResponse({
                'success': True,
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'is_superuser': user.is_superuser,
                    'groups': list(user.groups.values_list('name', flat=True)),
                },
                'message': 'Login successful'
            })
        return JsonResponse({'error': 'Invalid credentials'}, status=401)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def api_logout(request):
    if request.method == 'POST':
        from django.contrib.auth import logout
        logout(request)
        return JsonResponse({'success': True, 'message': 'Logged out successfully'})
    return JsonResponse({'error': 'POST method required'}, status=405)


# ============================================
# STUDENTS API
# ============================================

@require_api_auth
def api_students_list(request):
    students = Student.objects.filter(is_active=True).select_related('stream', 'grade_level')
    search = request.GET.get('search', '')
    stream_id = request.GET.get('stream_id')
    form = request.GET.get('form')
    risk_level = request.GET.get('risk_level')
    page = request.GET.get('page', 1)
    limit = int(request.GET.get('limit', 20))
    
    if search:
        students = students.filter(
            Q(name__icontains=search) | Q(admission_number__icontains=search)
        )
    if stream_id:
        students = students.filter(stream_id=stream_id)
    if form:
        students = students.filter(form=form)
    if risk_level:
        students = students.filter(risk_level=risk_level)
    
    paginator = Paginator(students.order_by('name'), limit)
    page_obj = paginator.get_page(page)
    
    data = [{
        'id': s.id,
        'admission_number': s.admission_number,
        'name': s.name,
        'form': s.form,
        'stream': s.stream.name if s.stream else None,
        'stream_id': s.stream.id if s.stream else None,
        'grade_level': s.grade_level.name if s.grade_level else None,
        'risk_score': s.risk_score,
        'risk_level': s.risk_level,
        'total_reports': s.total_reports,
        'intervention_count': s.intervention_count,
        'academic_status': s.academic_status,
        'days_since_last_incident': s.days_since_last_incident,
        'profile_picture': s.profile_picture.url if s.profile_picture else None,
    } for s in page_obj]
    
    return JsonResponse({
        'success': True,
        'students': data,
        'total': paginator.count,
        'pages': paginator.num_pages,
        'current_page': page_obj.number,
    })


@require_api_auth
def api_student_detail(request, student_id):
    try:
        student = Student.objects.select_related('stream', 'grade_level').get(id=student_id, is_active=True)
        reports = student.reports.select_related('category', 'reported_by').order_by('-reported_at')[:20]
        points = student.points_history.order_by('-created_at')[:20]
        rewards = student.rewards_earned.select_related('reward').order_by('-created_at')[:10]
        attendance = student.attendance_records.order_by('-date')[:30]
        documents = student.documents.order_by('-created_at')[:10]
        parents = StudentParentLink.objects.filter(student=student).select_related('parent__user')
        
        return JsonResponse({
            'success': True,
            'student': {
                'id': student.id,
                'admission_number': student.admission_number,
                'name': student.name,
                'form': student.form,
                'stream': student.stream.name if student.stream else None,
                'stream_id': student.stream.id if student.stream else None,
                'grade_level': student.grade_level.name if student.grade_level else None,
                'risk_score': student.risk_score,
                'risk_level': student.risk_level,
                'total_reports': student.total_reports,
                'intervention_count': student.intervention_count,
                'academic_status': student.academic_status,
                'days_since_last_incident': student.days_since_last_incident,
                'risk_trend': student.risk_trend,
                'enrollment_date': student.enrollment_date.isoformat() if student.enrollment_date else None,
                'profile_picture': student.profile_picture.url if student.profile_picture else None,
                'reports': [{
                    'id': r.id,
                    'category': r.category_name,
                    'rating': r.rating,
                    'points': r.points,
                    'comments': r.comments[:200] if r.comments else '',
                    'reported_at': r.reported_at.isoformat(),
                    'reported_by': r.reported_by.get_full_name() or r.reported_by.username,
                } for r in reports],
                'points_history': [{
                    'points': p.points,
                    'reason': p.reason,
                    'description': p.description[:100] if p.description else '',
                    'created_at': p.created_at.isoformat(),
                } for p in points],
                'rewards': [{
                    'name': r.reward.name,
                    'type': r.reward.reward_type,
                    'created_at': r.created_at.isoformat(),
                } for r in rewards],
                'attendance': [{
                    'date': a.date.isoformat(),
                    'status': a.status,
                    'checkin_method': a.checkin_method,
                    'check_in_time': a.check_in_time.isoformat() if a.check_in_time else None,
                } for a in attendance],
                'documents': [{
                    'id': d.id,
                    'type': d.document_type,
                    'title': d.title,
                    'url': d.file.url if d.file else None,
                    'created_at': d.created_at.isoformat(),
                } for d in documents],
                'parents': [{
                    'name': pl.parent.user.get_full_name() or pl.parent.user.username,
                    'relationship': pl.relationship,
                    'phone': pl.parent.phone_number,
                    'email': pl.parent.email,
                } for pl in parents],
            }
        })
    except Student.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Student not found'}, status=404)


@csrf_exempt
@require_api_auth
def api_create_student(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=405)
    try:
        data = json.loads(request.body)
        required = ['name', 'admission_number', 'stream_id']
        for field in required:
            if not data.get(field):
                return JsonResponse({'success': False, 'error': f'{field} is required'}, status=400)
        
        stream = Stream.objects.get(id=data['stream_id'], is_active=True)
        student = Student.objects.create(
            name=data['name'],
            admission_number=data['admission_number'],
            stream=stream,
            form=data.get('form', ''),
            year=data.get('year', timezone.now().year),
            optional_notes=data.get('notes', ''),
            grade_level_id=data.get('grade_level_id'),
            created_by=request.user if request.user.is_authenticated else None,
        )
        return JsonResponse({
            'success': True,
            'student': {
                'id': student.id,
                'name': student.name,
                'admission_number': student.admission_number,
                'risk_score': student.risk_score,
                'risk_level': student.risk_level,
            }
        })
    except Stream.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Stream not found'}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
@require_api_auth
def api_update_student(request, student_id):
    if request.method not in ['PUT', 'POST', 'PATCH']:
        return JsonResponse({'error': f'{request.method} method not supported for update'}, status=405)
    try:
        student = Student.objects.get(id=student_id, is_active=True)
        data = json.loads(request.body) if request.body else request.POST
        
        if 'name' in data:
            student.name = data['name']
        if 'form' in data:
            student.form = data['form']
        if 'year' in data:
            student.year = int(data['year'])
        if 'notes' in data:
            student.optional_notes = data['notes']
        if 'academic_status' in data:
            student.academic_status = data['academic_status']
        
        student.save()
        return JsonResponse({
            'success': True,
            'student': {
                'id': student.id,
                'name': student.name,
                'risk_score': student.risk_score,
                'risk_level': student.risk_level,
            }
        })
    except Student.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Student not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_api_auth
def api_delete_student(request, student_id):
    if request.method != 'POST' and request.method != 'DELETE':
        return JsonResponse({'error': 'POST or DELETE method required'}, status=405)
    try:
        student = Student.objects.get(id=student_id, is_active=True)
        student.is_active = False
        student.save()
        return JsonResponse({'success': True, 'message': 'Student deactivated'})
    except Student.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Student not found'}, status=404)


# ============================================
# REPORTS API
# ============================================

@require_api_auth
def api_reports_list(request):
    reports = DisciplineReport.objects.select_related('student', 'category', 'reported_by')
    student_id = request.GET.get('student_id')
    stream_id = request.GET.get('stream_id')
    category_id = request.GET.get('category_id')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    page = request.GET.get('page', 1)
    limit = int(request.GET.get('limit', 20))
    
    if student_id:
        reports = reports.filter(student_id=student_id)
    if stream_id:
        reports = reports.filter(student__stream_id=stream_id)
    if category_id:
        reports = reports.filter(category_id=category_id)
    if date_from:
        reports = reports.filter(reported_at__date__gte=date_from)
    if date_to:
        reports = reports.filter(reported_at__date__lte=date_to)
    
    paginator = Paginator(reports.order_by('-reported_at'), limit)
    page_obj = paginator.get_page(page)
    
    data = [{
        'id': r.id,
        'student_id': r.student.id,
        'student_name': r.student.name,
        'admission_number': r.student.admission_number,
        'category': r.category_name,
        'rating': r.rating,
        'points': r.points,
        'comments': r.comments[:200] if r.comments else '',
        'reported_at': r.reported_at.isoformat(),
        'reported_by': r.reported_by.get_full_name() or r.reported_by.username,
    } for r in page_obj]
    
    return JsonResponse({
        'success': True,
        'reports': data,
        'total': paginator.count,
        'pages': paginator.num_pages,
        'current_page': page_obj.number,
    })


@csrf_exempt
@require_api_auth
def api_create_report(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=405)
    try:
        data = json.loads(request.body)
        required = ['student_id', 'category_id', 'rating']
        for field in required:
            if not data.get(field):
                return JsonResponse({'success': False, 'error': f'{field} is required'}, status=400)
        
        student = Student.objects.get(id=data['student_id'], is_active=True)
        category = DisciplineCategory.objects.get(id=data['category_id'], is_active=True)
        reported_by = request.user if request.user.is_authenticated else User.objects.filter(is_superuser=True).first()
        
        report = DisciplineReport.objects.create(
            student=student,
            reported_by=reported_by,
            category=category,
            comments=data.get('comments', ''),
            rating=data['rating'],
        )
        
        return JsonResponse({
            'success': True,
            'report': {
                'id': report.id,
                'student_name': student.name,
                'category': report.category_name,
                'points': report.points,
                'rating': report.rating,
                'student_risk_score': student.risk_score,
                'student_risk_level': student.risk_level,
            }
        })
    except (Student.DoesNotExist, DisciplineCategory.DoesNotExist) as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================
# ATTENDANCE API
# ============================================

@require_api_auth
def api_attendance_list(request):
    attendance = AttendanceRecord.objects.all()
    student_id = request.GET.get('student_id')
    stream_id = request.GET.get('stream_id')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    status = request.GET.get('status')
    
    if student_id:
        attendance = attendance.filter(student_id=student_id)
    if stream_id:
        attendance = attendance.filter(stream_id=stream_id)
    if date_from:
        attendance = attendance.filter(date__gte=date_from)
    if date_to:
        attendance = attendance.filter(date__lte=date_to)
    if status:
        attendance = attendance.filter(status=status)
    
    data = [{
        'id': a.id,
        'student_id': a.student.id,
        'student_name': a.student.name,
        'stream': a.stream.name if a.stream else None,
        'form': a.form,
        'date': a.date.isoformat(),
        'status': a.status,
        'checkin_method': a.checkin_method,
        'check_in_time': a.check_in_time.isoformat() if a.check_in_time else None,
        'check_out_time': a.check_out_time.isoformat() if a.check_out_time else None,
        'remarks': a.remarks,
    } for a in attendance.order_by('-date')[:200]]
    
    return JsonResponse({'success': True, 'attendance': data})


@csrf_exempt
@require_api_auth
def api_mark_attendance(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=405)
    try:
        data = json.loads(request.body)
        student_id = data.get('student_id')
        stream_id = data.get('stream_id')
        form = data.get('form')
        date_str = data.get('date', timezone.now().date().isoformat())
        status = data.get('status', 'PRESENT')
        method = data.get('checkin_method', 'MANUAL')
        
        if not student_id or not stream_id:
            return JsonResponse({'success': False, 'error': 'student_id and stream_id required'}, status=400)
        
        student = Student.objects.get(id=student_id, is_active=True)
        stream = Stream.objects.get(id=stream_id, is_active=True)
        attendance_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        attendance, created = AttendanceRecord.objects.update_or_create(
            student=student, date=attendance_date,
            defaults={
                'stream': stream,
                'form': form or student.form,
                'status': status,
                'checkin_method': method,
                'qr_code_data': data.get('qr_code', ''),
                'check_in_time': timezone.now() if status == 'PRESENT' else None,
                'remarks': data.get('remarks', ''),
                'marked_by': request.user if request.user.is_authenticated else None,
            }
        )
        
        if created:
            StudentPoints.objects.create(
                student=student,
                points=10 if status == 'PRESENT' else 0,
                reason='PERFECT_ATTENDANCE' if status == 'PRESENT' else 'DISCIPLINE_REPORT',
                description=f"Attendance marked {status} for {attendance_date}",
            )
        
        return JsonResponse({
            'success': True,
            'attendance': {
                'id': attendance.id,
                'student_id': student.id,
                'date': attendance.date.isoformat(),
                'status': attendance.status,
                'created': created,
            }
        })
    except (Student.DoesNotExist, Stream.DoesNotExist) as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================
# TIMETABLE API
# ============================================

@require_api_auth
def api_timetable_list(request):
    timetable = TimetableEntry.objects.filter(is_active=True)
    stream_id = request.GET.get('stream_id')
    form = request.GET.get('form')
    day = request.GET.get('day')
    teacher_id = request.GET.get('teacher_id')
    
    if stream_id:
        timetable = timetable.filter(stream_id=stream_id)
    if form:
        timetable = timetable.filter(form=form)
    if day:
        timetable = timetable.filter(day=day)
    if teacher_id:
        timetable = timetable.filter(teacher_id=teacher_id)
    
    data = [{
        'id': t.id,
        'stream': t.stream.name,
        'stream_id': t.stream.id,
        'form': t.form,
        'subject': t.subject.name,
        'subject_id': t.subject.id,
        'teacher': t.teacher.get_full_name() or t.teacher.username if t.teacher else None,
        'day': t.day,
        'start_time': t.start_time.strftime('%H:%M'),
        'end_time': t.end_time.strftime('%H:%M'),
        'room': t.room,
        'notes': t.notes,
    } for t in timetable.order_by('day', 'start_time')]
    
    return JsonResponse({'success': True, 'timetable': data})


@csrf_exempt
@require_api_auth
def api_create_timetable_entry(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=405)
    try:
        data = json.loads(request.body)
        stream = Stream.objects.get(id=data['stream_id'], is_active=True)
        subject = Subject.objects.get(id=data['subject_id'])
        teacher = None
        if data.get('teacher_id'):
            teacher = User.objects.get(id=data['teacher_id'])
        
        entry = TimetableEntry.objects.create(
            school=stream.school,
            stream=stream,
            form=data['form'],
            subject=subject,
            teacher=teacher,
            day=data['day'],
            start_time=datetime.strptime(data['start_time'], '%H:%M').time(),
            end_time=datetime.strptime(data['end_time'], '%H:%M').time(),
            room=data.get('room', ''),
            notes=data.get('notes', ''),
        )
        return JsonResponse({
            'success': True,
            'entry': {
                'id': entry.id,
                'stream': entry.stream.name,
                'subject': entry.subject.name,
                'day': entry.day,
                'start_time': entry.start_time.strftime('%H:%M'),
                'end_time': entry.end_time.strftime('%H:%M'),
            }
        })
    except (Stream.DoesNotExist, Subject.DoesNotExist) as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================
# GAMIFICATION API
# ============================================

@require_api_auth
def api_leaderboard(request):
    period = request.GET.get('period', 'MONTHLY')
    stream_id = request.GET.get('stream_id')
    limit = int(request.GET.get('limit', 20))
    
    from .models import get_student_leaderboard
    entries = get_student_leaderboard(stream_id=stream_id, period=period, limit=limit)
    
    data = [{
        'rank': i + 1,
        'student_id': entry['student'].id,
        'student_name': entry['student'].name,
        'stream': entry['student'].stream.name if entry['student'].stream else None,
        'total_points': entry['total_points'],
        'rewards_count': entry['rewards_count'],
    } for i, entry in enumerate(entries)]
    
    return JsonResponse({'success': True, 'leaderboard': data, 'period': period})


@require_api_auth
def api_rewards_list(request):
    rewards = Reward.objects.filter(is_active=True)
    school_id = request.GET.get('school_id')
    if school_id:
        rewards = rewards.filter(school_id=school_id)
    
    data = [{
        'id': r.id,
        'name': r.name,
        'description': r.description,
        'reward_type': r.reward_type,
        'icon': r.icon,
        'color': r.color,
        'points_required': r.points_required,
    } for r in rewards.order_by('points_required')]
    
    return JsonResponse({'success': True, 'rewards': data})


@require_api_auth
def api_student_points(request, student_id):
    try:
        student = Student.objects.get(id=student_id, is_active=True)
        points_history = student.points_history.order_by('-created_at')[:50]
        
        total_positive = sum(p.points for p in points_history if p.points > 0)
        total_negative = sum(p.points for p in points_history if p.points < 0)
        net_points = total_positive + total_negative
        
        return JsonResponse({
            'success': True,
            'student_id': student.id,
            'student_name': student.name,
            'total_points': net_points,
            'positive_points': total_positive,
            'negative_points': total_negative,
            'points_history': [{
                'points': p.points,
                'reason': p.reason,
                'description': p.description[:100] if p.description else '',
                'created_at': p.created_at.isoformat(),
            } for p in points_history],
        })
    except Student.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Student not found'}, status=404)


# ============================================
# DOCUMENTS API
# ============================================

@require_api_auth
def api_documents_list(request, student_id):
    try:
        student = Student.objects.get(id=student_id, is_active=True)
        docs = student.documents.order_by('-created_at')
        
        data = [{
            'id': d.id,
            'document_type': d.document_type,
            'title': d.title,
            'description': d.description,
            'is_confidential': d.is_confidential,
            'file_size': d.file_size,
            'mime_type': d.mime_type,
            'url': d.file.url if d.file else None,
            'version': d.version,
            'uploaded_by': d.uploaded_by.get_full_name() or d.uploaded_by.username if d.uploaded_by else None,
            'created_at': d.created_at.isoformat(),
        } for d in docs]
        
        return JsonResponse({'success': True, 'documents': data, 'student_id': student_id})
    except Student.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Student not found'}, status=404)


@csrf_exempt
@require_api_auth
def api_upload_document(request, student_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST method required'}, status=405)
    try:
        student = Student.objects.get(id=student_id, is_active=True)
        if not request.FILES.get('file'):
            return JsonResponse({'success': False, 'error': 'No file provided'}, status=400)
        
        doc = DocumentVault.objects.create(
            student=student,
            document_type=request.POST.get('document_type', 'OTHER'),
            title=request.POST.get('title', 'Untitled'),
            description=request.POST.get('description', ''),
            file=request.FILES['file'],
            is_confidential=request.POST.get('is_confidential', 'false').lower() == 'true',
            uploaded_by=request.user if request.user.is_authenticated else None,
        )
        return JsonResponse({
            'success': True,
            'document': {
                'id': doc.id,
                'title': doc.title,
                'document_type': doc.document_type,
                'url': doc.file.url,
                'created_at': doc.created_at.isoformat(),
            }
        })
    except Student.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Student not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================
# ANALYTICS API
# ============================================

@require_api_auth
def api_analytics_overview(request):
    students = Student.objects.filter(is_active=True)
    total_students = students.count()
    total_reports = DisciplineReport.objects.count()
    
    return JsonResponse({
        'success': True,
        'overview': {
            'total_students': total_students,
            'total_reports': total_reports,
            'critical_count': students.filter(risk_level='CRITICAL').count(),
            'warning_count': students.filter(risk_level='WARNING').count(),
            'good_count': students.filter(risk_level='GOOD').count(),
            'avg_risk_score': round(students.aggregate(Avg('risk_score'))['risk_score__avg'] or 0, 1),
            'reports_today': DisciplineReport.objects.filter(reported_at__date=timezone.now().date()).count(),
            'online_teachers': TeacherProfile.objects.filter(is_online=True).count(),
            'attendance_rate': 0,
        }
    })


@require_api_auth
def api_category_distribution(request):
    categories = DisciplineReport.objects.values('category__name', 'category__key').annotate(
        count=Count('id'), avg_points=Avg('points')
    ).order_by('-count')[:15]
    
    return JsonResponse({'success': True, 'categories': list(categories)})


@require_api_auth
def api_trend_analysis(request):
    days = int(request.GET.get('days', 30))
    start_date = timezone.now() - timedelta(days=days)
    
    daily_reports = DisciplineReport.objects.filter(
        reported_at__gte=start_date
    ).values('reported_at__date').annotate(count=Count('id')).order_by('reported_at__date')
    
    return JsonResponse({
        'success': True,
        'trends': list(daily_reports),
        'period_days': days,
    })


# ============================================
# PARENT PORTAL API
# ============================================

@require_api_auth
def api_parent_children(request):
    try:
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Authentication required'}, status=401)
        
        parent = request.user.parent_profile
        links = StudentParentLink.objects.filter(parent=parent).select_related('student__stream')
        
        children = []
        for link in links:
            student = link.student
            children.append({
                'id': student.id,
                'name': student.name,
                'admission_number': student.admission_number,
                'stream': student.stream.name if student.stream else None,
                'form': student.form,
                'risk_score': student.risk_score,
                'risk_level': student.risk_level,
                'relationship': link.relationship,
                'can_pickup': link.can_pickup,
            })
        
        return JsonResponse({'success': True, 'children': children})
    except ParentProfile.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Parent profile not found'}, status=404)


@require_api_auth
def api_parent_notifications(request):
    try:
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Authentication required'}, status=401)
        
        parent = request.user.parent_profile
        links = StudentParentLink.objects.filter(parent=parent).values_list('student_id', flat=True)
        
        notifications = Notification.objects.filter(
            student_id__in=links,
            is_read=False,
        ).order_by('-created_at')[:20]
        
        data = [{
            'id': n.id,
            'title': n.title,
            'message': n.message[:200],
            'type': n.notification_type,
            'student_name': n.student.name if n.student else None,
            'created_at': n.created_at.isoformat(),
        } for n in notifications]
        
        return JsonResponse({'success': True, 'notifications': data})
    except ParentProfile.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Parent profile not found'}, status=404)


# ============================================
# DASHBOARD API
# ============================================

@require_api_auth
def api_dashboard_data(request):
    students = Student.objects.filter(is_active=True)
    reports = DisciplineReport.objects.all()
    
    data = {
        'students': {
            'total': students.count(),
            'critical': students.filter(risk_level='CRITICAL').count(),
            'warning': students.filter(risk_level='WARNING').count(),
            'good': students.filter(risk_level='GOOD').count(),
        },
        'reports': {
            'total': reports.count(),
            'today': reports.filter(reported_at__date=timezone.now().date()).count(),
            'this_week': reports.filter(reported_at__gte=timezone.now() - timedelta(days=7)).count(),
            'this_month': reports.filter(reported_at__gte=timezone.now() - timedelta(days=30)).count(),
        },
        'attendance': {
            'present': AttendanceRecord.objects.filter(status='PRESENT').count(),
            'absent': AttendanceRecord.objects.filter(status='ABSENT').count(),
            'late': AttendanceRecord.objects.filter(status='LATE').count(),
        },
        'gamification': {
            'total_points_awarded': StudentPoints.objects.aggregate(total=Sum('points'))['total'] or 0,
            'total_rewards': StudentReward.objects.count(),
            'total_points_entries': StudentPoints.objects.count(),
        },
        'recent_activity': []
    }
    
    recent_reports = reports.select_related('student', 'category').order_by('-reported_at')[:10]
    for r in recent_reports:
        data['recent_activity'].append({
            'type': 'report',
            'message': f"{r.student.name}: {r.category_name} (+{r.points} pts)",
            'time': r.reported_at.isoformat(),
        })
    
    recent_points = StudentPoints.objects.order_by('-created_at')[:10]
    for p in recent_points:
        data['recent_activity'].append({
            'type': 'points',
            'message': f"{p.student.name}: {p.points:+.0f} pts ({p.reason})",
            'time': p.created_at.isoformat(),
        })
    
    data['recent_activity'].sort(key=lambda x: x['time'], reverse=True)
    data['recent_activity'] = data['recent_activity'][:20]
    
    return JsonResponse({'success': True, 'data': data})


# ============================================
# HEALTH CHECK API
# ============================================

def api_health(request):
    return JsonResponse({
        'status': 'healthy',
        'timestamp': timezone.now().isoformat(),
        'version': '2.0.0',
        'features': {
            'students': True,
            'reports': True,
            'attendance': True,
            'timetable': True,
            'gamification': True,
            'documents': True,
            'ai': True,
            'notifications': True,
            'parent_portal': True,
        }
    })
