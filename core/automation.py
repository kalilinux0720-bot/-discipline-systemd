"""
Automation Engine Module
Handles automated disciplinary workflows:
- Auto-send reports to parents
- Auto-assign penalties based on thresholds
- Auto-notify parents via email/SMS
- Scheduled automation tasks
"""
import json
import os
import logging
from datetime import datetime, timedelta, time as dt_time
from django.conf import settings
from django.db.models import Count, Sum, Avg, Q, F
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.management import call_command

from .models import (
    Student, DisciplineReport, DisciplineCategory, Notification,
    Stream, TeacherProfile, StudentPoints, Reward, StudentReward,
    AttendanceRecord, School, StudentParentLink, PasswordReset,
)
from .notifications import NotificationOrchestrator, EmailNotificationService, SMSNotificationService

logger = logging.getLogger(__name__)


# ============================================
# AUTO-REPORT GENERATION
# ============================================

class AutoReportGenerator:
    """Automatically generates and sends discipline reports"""

    def __init__(self):
        self.orchestrator = NotificationOrchestrator()

    def generate_daily_summary(self):
        try:
            today = timezone.now().date()
            reports = DisciplineReport.objects.filter(reported_at__date=today).select_related('student', 'category', 'reported_by')

            summary = {
                'date': today.isoformat(),
                'total_reports': reports.count(),
                'by_category': {},
                'by_stream': {},
                'critical_students': [],
            }

            for report in reports:
                cat_name = report.category_name
                summary['by_category'][cat_name] = summary['by_category'].get(cat_name, 0) + 1

                stream_name = report.student.stream.name if report.student.stream else 'Unknown'
                summary['by_stream'][stream_name] = summary['by_stream'].get(stream_name, 0) + 1

                if report.student.risk_score >= 60:
                    summary['critical_students'].append({
                        'name': report.student.name,
                        'risk_score': report.student.risk_score,
                        'category': cat_name,
                    })

            admins = User.objects.filter(is_superuser=True)
            for admin in admins:
                Notification.objects.create(
                    title=f"Daily Discipline Summary - {today.strftime('%B %d, %Y')}",
                    message=f"Total reports today: {summary['total_reports']}\n"
                            f"Critical students: {len(summary['critical_students'])}\n"
                            f"Top category: {max(summary['by_category'], key=summary['by_category'].get) if summary['by_category'] else 'N/A'}",
                    notification_type='info',
                )

            logger.info(f"Daily summary generated: {summary['total_reports']} reports")
            return summary
        except Exception as e:
            logger.error(f"Daily summary error: {str(e)}")
            return {'error': str(e)}

    def generate_weekly_report(self):
        try:
            end_date = timezone.now().date()
            start_date = end_date - timedelta(days=7)
            reports = DisciplineReport.objects.filter(
                reported_at__date__range=(start_date, end_date)
            ).select_related('student', 'category')

            summary = {
                'period': f"{start_date} to {end_date}",
                'total_reports': reports.count(),
                'top_offenses': list(reports.values('category__name').annotate(
                    count=Count('id')
                ).order_by('-count')[:5]),
                'critical_students': list(
                    Student.objects.filter(risk_level='CRITICAL').values('name', 'risk_score', 'stream__name')[:10]
                ),
            }

            admins = User.objects.filter(is_superuser=True)
            for admin in admins:
                Notification.objects.create(
                    title=f"Weekly Discipline Report - {end_date.strftime('%B %d, %Y')}",
                    message=f"Weekly report: {summary['total_reports']} total reports\n"
                            f"Critical students: {len(summary['critical_students'])}",
                    notification_type='info',
                )

            return summary
        except Exception as e:
            logger.error(f"Weekly report error: {str(e)}")
            return {'error': str(e)}


# ============================================
# AUTO-PENALTY ASSIGNMENT
# ============================================

class AutoPenaltyAssigner:
    """Automatically assigns penalties based on point thresholds"""

    PENALTY_THRESHOLDS = [
        (50, 'WARNING - Parent Meeting Required'),
        (80, 'SUSPENSION_3_DAYS - 3 Day Suspension'),
        (120, 'SUSPENSION_1_WEEK - 1 Week Suspension'),
        (200, 'SUSPENSION_2_WEEKS - 2 Week Suspension'),
        (300, 'EXPULSION_RECOMMENDATION - Expulsion Review'),
    ]

    def __init__(self):
        self.orchestrator = NotificationOrchestrator()

    def check_and_assign_penalties(self):
        try:
            students = Student.objects.filter(is_active=True).filter(risk_score__gte=50)
            assigned = []

            for student in students:
                existing_penalty = student.points_history.filter(
                    reason='PENALTY',
                    created_at__date=timezone.now().date()
                ).exists()
                if existing_penalty:
                    continue

                for threshold, penalty_name in self.PENALTY_THRESHOLDS:
                    if student.risk_score >= threshold:
                        StudentPoints.objects.create(
                            student=student,
                            points=0,
                            reason='PENALTY',
                            description=f"Auto-assigned penalty: {penalty_name} (Risk Score: {student.risk_score})",
                        )

                        notification = Notification.objects.create(
                            title=f"Auto-Penalty: {student.name}",
                            message=f"Student {student.name} has reached risk score {student.risk_score}%. "
                                    f"Penalty assigned: {penalty_name}",
                            notification_type='warning',
                            student=student,
                        )
                        admins = User.objects.filter(is_superuser=True)
                        notification.target_users.set(admins)

                        parent_links = StudentParentLink.objects.filter(student=student, receive_notifications=True)
                        for link in parent_links:
                            parent = link.parent
                            if parent.receive_sms and parent.phone_number:
                                SMSNotificationService.send_sms(
                                    parent.phone_number,
                                    f"ALERT: {student.name} has been assigned a penalty: {penalty_name}. "
                                    f"Risk Score: {student.risk_score}%. Contact school administration."
                                )

                        assigned.append({
                            'student': student.name,
                            'risk_score': student.risk_score,
                            'penalty': penalty_name,
                        })
                        break

            logger.info(f"Penalty check complete: {len(assigned)} penalties assigned")
            return {'success': True, 'assigned': assigned}
        except Exception as e:
            logger.error(f"Penalty assignment error: {str(e)}")
            return {'success': False, 'error': str(e)}


# ============================================
# AUTO-NOTIFICATION SERVICE
# ============================================

class AutoNotificationService:
    """Automatically sends notifications for various events"""

    def __init__(self):
        self.orchestrator = NotificationOrchestrator()

    def send_attendance_notifications(self, date=None):
        try:
            if date is None:
                date = timezone.now().date()

            absent_records = AttendanceRecord.objects.filter(date=date, status='ABSENT')
            notified = 0

            for record in absent_records:
                self.orchestrator.notify_attendance(record.student, 'ABSENT', date)
                notified += 1

            late_records = AttendanceRecord.objects.filter(date=date, status='LATE')
            for record in late_records:
                self.orchestrator.notify_attendance(record.student, 'LATE', date)
                notified += 1

            logger.info(f"Attendance notifications sent: {notified}")
            return {'success': True, 'notified': notified}
        except Exception as e:
            logger.error(f"Attendance notification error: {str(e)}")
            return {'success': False, 'error': str(e)}

    def send_risk_level_notifications(self):
        try:
            critical_students = Student.objects.filter(is_active=True, risk_level='CRITICAL')
            notified = 0

            for student in critical_students:
                existing_critical_notification = Notification.objects.filter(
                    student=student,
                    notification_type='critical',
                    created_at__date=timezone.now().date()
                ).exists()
                if not existing_critical_notification:
                    self.orchestrator.notify_critical_alert(student)
                    notified += 1

            logger.info(f"Risk level notifications sent: {notified}")
            return {'success': True, 'notified': notified}
        except Exception as e:
            logger.error(f"Risk notification error: {str(e)}")
            return {'success': False, 'error': str(e)}

    def send_intervention_reminders(self):
        try:
            students_needing_intervention = Student.objects.filter(
                is_active=True,
                risk_level__in=['WARNING', 'CRITICAL'],
                intervention_count=0,
            )

            reminded = 0
            for student in students_needing_intervention:
                class_teachers = User.objects.filter(
                    teacher_profile__assigned_stream=student.stream,
                    groups__name='ClassTeacher',
                )

                if class_teachers.exists():
                    notification = Notification.objects.create(
                        title=f"Intervention Needed: {student.name}",
                        message=f"Student {student.name} ({student.risk_level}, Score: {student.risk_score}) "
                                f"has no interventions recorded. Please create an intervention plan.",
                        notification_type='warning',
                        student=student,
                    )
                    notification.target_users.set(class_teachers)
                    reminded += 1

            logger.info(f"Intervention reminders sent: {reminded}")
            return {'success': True, 'reminded': reminded}
        except Exception as e:
            logger.error(f"Intervention reminder error: {str(e)}")
            return {'success': False, 'error': str(e)}

    def send_weekly_reward_reminders(self):
        try:
            students_with_points = Student.objects.filter(
                is_active=True,
                points_history__isnull=False,
            ).distinct()

            reminded = 0
            for student in students_with_points:
                total_points = sum(p.points for p in student.points_history.all())
                earned_rewards = student.rewards_earned.values_list('reward_id', flat=True)
                available_rewards = Reward.objects.filter(
                    school=student.stream.school if student.stream.school else None,
                    points_required__lte=total_points,
                    is_active=True,
                ).exclude(id__in=earned_rewards)

                if available_rewards.exists():
                    class_teachers = User.objects.filter(
                        teacher_profile__assigned_stream=student.stream,
                        groups__name='ClassTeacher',
                    )

                    if class_teachers.exists():
                        reward_names = ', '.join([r.name for r in available_rewards[:3]])
                        notification = Notification.objects.create(
                            title=f"Reward Available: {student.name}",
                            message=f"{student.name} has {total_points} points and can earn: {reward_names}",
                            notification_type='success',
                            student=student,
                        )
                        notification.target_users.set(class_teachers)
                        reminded += 1

            logger.info(f"Reward reminders sent: {reminded}")
            return {'success': True, 'reminded': reminded}
        except Exception as e:
            logger.error(f"Reward reminder error: {str(e)}")
            return {'success': False, 'error': str(e)}


# ============================================
# REWARD AUTOMATION
# ============================================

class RewardAutomation:
    """Automatically awards rewards based on achievements"""

    @staticmethod
    def check_and_award_rewards(student_id):
        try:
            student = Student.objects.get(id=student_id, is_active=True)
            school = School.objects.first()
            if not school:
                return {'success': False, 'error': 'No school configured'}

            total_points = sum(p.points for p in student.points_history.all())
            earned_rewards = student.rewards_earned.values_list('reward_id', flat=True)

            available_rewards = Reward.objects.filter(
                school=school,
                points_required__lte=total_points,
                is_active=True,
            ).exclude(id__in=earned_rewards)

            awarded = []
            for reward in available_rewards:
                StudentReward.objects.create(
                    student=student,
                    reward=reward,
                    awarded_by=User.objects.filter(is_superuser=True).first(),
                )
                Notification.objects.create(
                    title=f"Reward Earned: {student.name}",
                    message=f"{student.name} earned the '{reward.name}' reward!",
                    notification_type='success',
                    student=student,
                )
                parent_links = StudentParentLink.objects.filter(student=student, receive_notifications=True)
                for link in parent_links:
                    parent = link.parent
                    if parent.receive_email and parent.email:
                        EmailNotificationService.send_reward_notification(
                            parent.email, student.name, reward.name
                        )
                awarded.append(reward.name)

            return {'success': True, 'awarded': awarded}
        except Exception as e:
            logger.error(f"Reward automation error: {str(e)}")
            return {'success': False, 'error': str(e)}

    @staticmethod
    def award_perfect_attendance():
        try:
            school = School.objects.first()
            if not school:
                return {'success': False, 'error': 'No school configured'}

            today = timezone.now().date()
            month_start = today.replace(day=1)

            students_with_perfect_attendance = []
            for student in Student.objects.filter(is_active=True):
                attendance = AttendanceRecord.objects.filter(
                    student=student,
                    date__range=(month_start, today),
                ).exclude(status__in=['ABSENT', 'EXCUSED', 'LEAVE'])

                working_days = 0
                current = month_start
                while current <= today:
                    if current.weekday() < 5:
                        working_days += 1
                    current += timedelta(days=1)

                if working_days > 0 and attendance.count() >= working_days:
                    students_with_perfect_attendance.append(student)

            rewarded = []
            for student in students_with_perfect_attendance:
                StudentPoints.objects.create(
                    student=student,
                    points=50,
                    reason='PERFECT_ATTENDANCE',
                    description=f"Perfect attendance for {timezone.now().strftime('%B %Y')}",
                )
                rewarded.append(student.name)

            logger.info(f"Perfect attendance rewards: {len(rewarded)} students")
            return {'success': True, 'rewarded': rewarded}
        except Exception as e:
            logger.error(f"Perfect attendance error: {str(e)}")
            return {'success': False, 'error': str(e)}


# ============================================
# DATA CLEANUP
# ============================================

class DataCleanupService:
    """Automated data cleanup and maintenance"""

    @staticmethod
    def cleanup_old_notifications(days=30):
        try:
            cutoff = timezone.now() - timedelta(days=days)
            deleted, _ = Notification.objects.filter(
                created_at__lt=cutoff,
                is_read=True,
            ).delete()
            logger.info(f"Cleaned up {deleted} old notifications")
            return {'success': True, 'deleted': deleted}
        except Exception as e:
            logger.error(f"Cleanup error: {str(e)}")
            return {'success': False, 'error': str(e)}

    @staticmethod
    def archive_old_reports(months=12):
        try:
            cutoff = timezone.now() - timedelta(days=months * 30)
            old_reports = DisciplineReport.objects.filter(reported_at__lt=cutoff)
            count = old_reports.count()
            logger.info(f"Found {count} reports older than {months} months (archiving not yet implemented)")
            return {'success': True, 'archived': 0, 'pending': count}
        except Exception as e:
            logger.error(f"Archive error: {str(e)}")
            return {'success': False, 'error': str(e)}

    @staticmethod
    def update_leaderboards():
        try:
            from .models import LeaderboardEntry
            school = School.objects.first()
            if not school:
                return {'success': False, 'error': 'No school configured'}

            now = timezone.now()
            periods = [
                ('WEEKLY', now - timedelta(days=7), now),
                ('MONTHLY', now - timedelta(days=30), now),
            ]

            updated = 0
            for period_name, start, end in periods:
                LeaderboardEntry.objects.filter(
                    school=school, period=period_name, period_start=start.date(), period_end=end.date()
                ).delete()

                students = []
                for student in Student.objects.filter(is_active=True):
                    points = student.points_history.filter(created_at__date__range=(start.date(), end.date()))
                    total = sum(p.points for p in points)
                    students.append((student, total))

                students.sort(key=lambda x: x[1], reverse=True)
                for rank, (student, total_points) in enumerate(students, 1):
                    LeaderboardEntry.objects.create(
                        student=student,
                        school=school,
                        period=period_name,
                        period_start=start.date(),
                        period_end=end.date(),
                        total_points=total_points,
                        rank=rank,
                    )
                    updated += 1

            return {'success': True, 'updated': updated}
        except Exception as e:
            logger.error(f"Leaderboard update error: {str(e)}")
            return {'success': False, 'error': str(e)}


# ============================================
# AUTOMATION RUNNER
# ============================================

class AutomationRunner:
    """Run all automation tasks"""

    def __init__(self):
        self.report_gen = AutoReportGenerator()
        self.penalty_assigner = AutoPenaltyAssigner()
        self.notification_service = AutoNotificationService()
        self.reward_automation = RewardAutomation()
        self.cleanup_service = DataCleanupService()

    def run_daily_tasks(self):
        try:
            results = {
                'daily_summary': self.report_gen.generate_daily_summary(),
                'penalties': self.penalty_assigner.check_and_assign_penalties(),
                'risk_notifications': self.notification_service.send_risk_level_notifications(),
                'attendance_notifications': self.notification_service.send_attendance_notifications(),
                'intervention_reminders': self.notification_service.send_intervention_reminders(),
                'reward_reminders': self.notification_service.send_weekly_reward_reminders(),
            }
            logger.info(f"Daily automation tasks completed: {list(results.keys())}")
            return {'success': True, 'results': results}
        except Exception as e:
            logger.error(f"Daily automation error: {str(e)}")
            return {'success': False, 'error': str(e)}

    def run_weekly_tasks(self):
        try:
            results = {
                'weekly_report': self.report_gen.generate_weekly_report(),
                'leaderboard_update': self.cleanup_service.update_leaderboards(),
                'perfect_attendance': self.reward_automation.award_perfect_attendance(),
            }
            logger.info(f"Weekly automation tasks completed: {list(results.keys())}")
            return {'success': True, 'results': results}
        except Exception as e:
            logger.error(f"Weekly automation error: {str(e)}")
            return {'success': False, 'error': str(e)}

    def run_maintenance_tasks(self):
        try:
            results = {
                'cleanup': self.cleanup_service.cleanup_old_notifications(),
                'archive_check': self.cleanup_service.archive_old_reports(),
            }
            logger.info(f"Maintenance tasks completed: {list(results.keys())}")
            return {'success': True, 'results': results}
        except Exception as e:
            logger.error(f"Maintenance error: {str(e)}")
            return {'success': False, 'error': str(e)}


# ============================================
# AUTOMATION MANAGEMENT COMMANDS
# ============================================

def run_automation(task_type='daily'):
    runner = AutomationRunner()
    if task_type == 'daily':
        return runner.run_daily_tasks()
    elif task_type == 'weekly':
        return runner.run_weekly_tasks()
    elif task_type == 'maintenance':
        return runner.run_maintenance_tasks()
    else:
        return {'success': False, 'error': f'Unknown task type: {task_type}'}
