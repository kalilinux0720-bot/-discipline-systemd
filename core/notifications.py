"""
Notifications Service Module
Handles Email (SendGrid), SMS (Africa's Talking), and In-App notifications.
"""
import json
import os
import logging
from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models import Q

from .models import Student, ParentProfile, StudentParentLink, Notification, School, DisciplineReport, Reward

logger = logging.getLogger(__name__)


# ============================================
# EMAIL NOTIFICATIONS (SendGrid)
# ============================================

class EmailNotificationService:
    """Email notification service using SendGrid or Django SMTP"""
    
    @staticmethod
    def send_email(to_email, subject, html_content, text_content=None):
        try:
            if text_content is None:
                text_content = strip_tags(html_content)
            
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@discipline-webster.com'),
                to=[to_email],
            )
            email.attach_alternative(html_content, 'text/html')
            email.send(fail_silently=False)
            logger.info(f"Email sent to {to_email}: {subject}")
            return {'success': True, 'message': 'Email sent successfully'}
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def send_report_notification(parent_email, student_name, category, points, rating):
        school = School.objects.first()
        subject = f"Disciplinary Report: {student_name} - {school.name if school else 'School'}"
        html_content = render_to_string('emails/discipline_report.html', {
            'student_name': student_name,
            'category': category,
            'points': points,
            'rating': rating,
            'school': school,
            'date': timezone.now().strftime('%Y-%m-%d %H:%M'),
        })
        return EmailNotificationService.send_email(parent_email, subject, html_content)
    
    @staticmethod
    def send_risk_alert(parent_email, student_name, risk_score, risk_level):
        school = School.objects.first()
        subject = f"IMPORTANT: Risk Alert for {student_name} - {school.name if school else 'School'}"
        html_content = render_to_string('emails/risk_alert.html', {
            'student_name': student_name,
            'risk_score': risk_score,
            'risk_level': risk_level,
            'school': school,
            'date': timezone.now().strftime('%Y-%m-%d %H:%M'),
        })
        return EmailNotificationService.send_email(parent_email, subject, html_content)
    
    @staticmethod
    def send_weekly_summary(parent_email, parent_name, student_name, week_stats):
        school = School.objects.first()
        subject = f"Weekly Summary for {student_name} - {school.name if school else 'School'}"
        html_content = render_to_string('emails/weekly_summary.html', {
            'parent_name': parent_name,
            'student_name': student_name,
            'week_stats': week_stats,
            'school': school,
            'date': timezone.now().strftime('%Y-%m-%d'),
        })
        return EmailNotificationService.send_email(parent_email, subject, html_content)
    
    @staticmethod
    def send_reward_notification(parent_email, student_name, reward_name):
        school = School.objects.first()
        subject = f"Congratulations! {student_name} earned a reward - {school.name if school else 'School'}"
        html_content = render_to_string('emails/reward_earned.html', {
            'student_name': student_name,
            'reward_name': reward_name,
            'school': school,
            'date': timezone.now().strftime('%Y-%m-%d'),
        })
        return EmailNotificationService.send_email(parent_email, subject, html_content)
    
    @staticmethod
    def send_password_reset(user_email, username, new_password):
        subject = 'Password Reset - Discipline Management System'
        html_content = render_to_string('emails/password_reset.html', {
            'username': username,
            'new_password': new_password,
        })
        return EmailNotificationService.send_email(user_email, subject, html_content)


# ============================================
# SMS NOTIFICATIONS (Africa's Talking)
# ============================================

class SMSNotificationService:
    """SMS notification service using Africa's Talking API"""
    
    def __init__(self):
        self.api_key = os.environ.get('AFRICAS_TALKING_API_KEY', '')
        self.username = os.environ.get('AFRICAS_TALKING_USERNAME', '')
        self.sender_id = os.environ.get('AFRICAS_TALKING_SENDER_ID', 'SCHOOL')
    
    def send_sms(self, phone_number, message):
        try:
            import requests
            
            if not self.api_key or not self.username:
                logger.warning("Africa's Talking credentials not configured. SMS not sent.")
                return {'success': False, 'error': 'SMS service not configured'}
            
            url = 'https://api.africastalking.com/restless/send'
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
            }
            data = {
                'username': self.username,
                'to': phone_number,
                'message': message,
                'from': self.sender_id,
            }
            
            response = requests.post(url, headers=headers, data=data, timeout=30)
            if response.status_code == 200:
                logger.info(f"SMS sent to {phone_number}")
                return {'success': True, 'message': 'SMS sent successfully'}
            else:
                logger.error(f"SMS failed for {phone_number}: {response.status_code} - {response.text}")
                return {'success': False, 'error': f'SMS API error: {response.status_code}'}
        except Exception as e:
            logger.error(f"SMS error for {phone_number}: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def send_report_sms(phone_number, student_name, category, risk_level):
        message = f"ALERT: {student_name} received a disciplinary report for '{category}'. Risk Level: {risk_level}. Check app for details. - Discipline Webster"
        service = SMSNotificationService()
        return service.send_sms(phone_number, message)
    
    @staticmethod
    def send_risk_alert_sms(phone_number, student_name, risk_score):
        message = f"URGENT: {student_name} risk level has increased to {risk_score}%. Please contact the school administration immediately. - Discipline Webster"
        service = SMSNotificationService()
        return service.send_sms(phone_number, message)
    
    @staticmethod
    def send_attendance_sms(phone_number, student_name, status, date):
        message = f"ATTENDANCE: {student_name} was marked {status} on {date}. - Discipline Webster"
        service = SMSNotificationService()
        return service.send_sms(phone_number, message)


# ============================================
# PUSH NOTIFICATIONS (Web Push)
# ============================================

class PushNotificationService:
    """Push notification service for web and mobile"""
    
    @staticmethod
    def send_push(user_id, title, body, data=None):
        try:
            notification = Notification.objects.create(
                title=title,
                message=body,
                notification_type='info',
            )
            notification.target_users.add(User.objects.get(id=user_id))
            
            logger.info(f"Push notification created for user {user_id}: {title}")
            return {'success': True, 'message': 'Push notification sent'}
        except Exception as e:
            logger.error(f"Push notification error: {str(e)}")
            return {'success': False, 'error': str(e)}


# ============================================
# NOTIFICATION ORCHESTRATOR
# ============================================

class NotificationOrchestrator:
    """Coordinates all notification channels"""
    
    def __init__(self):
        self.email_service = EmailNotificationService()
        self.sms_service = SMSNotificationService()
        self.push_service = PushNotificationService()
    
    def notify_report_created(self, report):
        try:
            student = report.student
            category = report.category_name
            points = report.points
            rating = report.get_rating_display() if hasattr(report, 'get_rating_display') else dict(DisciplineReport.RATING_CHOICES).get(report.rating, report.rating)
            
            in_app_notification = Notification.objects.create(
                title=f"New Report: {student.name}",
                message=f"{student.name} received a report for '{category}'. Points: +{points}. Rating: {rating}. Risk Level: {student.risk_level}",
                notification_type='warning',
                student=student,
            )
            in_app_notification.target_users.add(report.reported_by)
            
            parent_links = StudentParentLink.objects.filter(student=student, receive_notifications=True)
            for link in parent_links:
                parent = link.parent
                if parent.receive_sms and parent.phone_number:
                    SMSNotificationService.send_report_sms(
                        parent.phone_number, student.name, category, student.risk_level
                    )
                if parent.receive_email and parent.email:
                    EmailNotificationService.send_report_notification(
                        parent.email, student.name, category, points, rating
                    )
            
            if student.risk_score >= 60:
                self.notify_critical_alert(student)
            
            return {'success': True, 'message': 'Report notifications sent'}
        except Exception as e:
            logger.error(f"Notification error: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def notify_critical_alert(self, student):
        try:
            notification = Notification.objects.create(
                title=f"CRITICAL ALERT: {student.name}",
                message=f"Student {student.name} has reached CRITICAL risk level ({student.risk_score}%). Immediate action required.",
                notification_type='critical',
                student=student,
            )
            admins = User.objects.filter(is_superuser=True)
            notification.target_users.set(admins)
            
            parent_links = StudentParentLink.objects.filter(student=student, receive_notifications=True)
            for link in parent_links:
                parent = link.parent
                if parent.receive_sms and parent.phone_number:
                    SMSNotificationService.send_risk_alert_sms(
                        parent.phone_number, student.name, student.risk_score
                    )
                if parent.receive_email and parent.email:
                    EmailNotificationService.send_risk_alert(
                        parent.email, student.name, student.risk_score, student.risk_level
                    )
            
            return {'success': True}
        except Exception as e:
            logger.error(f"Critical alert error: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def notify_reward_earned(self, student, reward):
        try:
            notification = Notification.objects.create(
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
            return {'success': True}
        except Exception as e:
            logger.error(f"Reward notification error: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def notify_attendance(self, student, status, date):
        try:
            parent_links = StudentParentLink.objects.filter(student=student, receive_notifications=True)
            for link in parent_links:
                parent = link.parent
                if parent.receive_sms and parent.phone_number:
                    SMSNotificationService.send_attendance_sms(
                        parent.phone_number, student.name, status, str(date)
                    )
            return {'success': True}
        except Exception as e:
            logger.error(f"Attendance notification error: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def send_bulk_notification(self, user_ids, title, message, notification_type='info'):
        try:
            notification = Notification.objects.create(
                title=title,
                message=message,
                notification_type=notification_type,
            )
            users = User.objects.filter(id__in=user_ids)
            notification.target_users.set(users)
            return {'success': True, 'message': f'Notification sent to {users.count()} users'}
        except Exception as e:
            logger.error(f"Bulk notification error: {str(e)}")
            return {'success': False, 'error': str(e)}


# ============================================
# UTILITY FUNCTIONS
# ============================================

def send_notification_email(to_email, subject, template_name, context):
    try:
        html_content = render_to_string(f'emails/{template_name}.html', context)
        return EmailNotificationService.send_email(to_email, subject, html_content)
    except Exception as e:
        logger.error(f"Email template error: {str(e)}")
        return {'success': False, 'error': str(e)}


def send_notification_sms(phone_number, message):
    return SMSNotificationService.send_sms(phone_number, message)


def notify_parents_of_student(student_id, title, message, notification_type='info'):
    try:
        student = Student.objects.get(id=student_id, is_active=True)
        notification = Notification.objects.create(
            title=title,
            message=message,
            notification_type=notification_type,
            student=student,
        )
        parent_links = StudentParentLink.objects.filter(student=student, receive_notifications=True)
        parents = [link.parent.user for link in parent_links]
        notification.target_users.set(parents)
        
        orchestrator = NotificationOrchestrator()
        for link in parent_links:
            parent = link.parent
            if parent.receive_email and parent.email:
                context = {
                    'student_name': student.name,
                    'message': message,
                    'school': School.objects.first(),
                }
                send_notification_email(parent.email, title, 'generic_notification', context)
        
        return {'success': True, 'notification_id': notification.id}
    except Student.DoesNotExist:
        return {'success': False, 'error': 'Student not found'}
    except Exception as e:
        logger.error(f"Parent notification error: {str(e)}")
        return {'success': False, 'error': str(e)}