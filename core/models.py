from django.db import models, transaction
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models import Sum
from django.utils import timezone
from datetime import timedelta


class ActiveObjectsManager(models.Manager):
    """Default manager that hides soft-deleted records."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

# ============================================
# SCHOOL SETUP MODELS
# ============================================

class School(models.Model):
    name = models.CharField(max_length=200, help_text="School name")
    short_name = models.CharField(max_length=50, blank=True)
    motto = models.CharField(max_length=200, blank=True)
    logo = models.ImageField(upload_to='school/', null=True, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    current_year = models.IntegerField(default=timezone.now().year)
    terms_per_year = models.IntegerField(default=3, choices=[(1, '1 Term'), (2, '2 Terms'), (3, '3 Terms'), (4, '4 Terms')])
    allow_teacher_registration = models.BooleanField(default=True)
    require_teacher_approval = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = "School Settings"
    
    def __str__(self):
        return self.name


class GradeLevel(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='grades')
    name = models.CharField(max_length=20)
    code = models.CharField(max_length=10)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['order', 'name']
        unique_together = ['school', 'name']
    
    def __str__(self):
        return f"{self.name} ({self.school.name})"


class AcademicTerm(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='terms')
    name = models.CharField(max_length=50)
    term_number = models.IntegerField()
    year = models.IntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    is_current = models.BooleanField(default=False)
    notes = models.TextField(blank=True, null=True)
    
    def get_duration_days(self):
        if self.start_date and self.end_date:
            delta = self.end_date - self.start_date
            return delta.days
        return 0
    
    def get_duration_weeks(self):
        days = self.get_duration_days()
        return round(days / 7, 1)
    
    def get_duration_months(self):
        days = self.get_duration_days()
        return round(days / 30.44, 1)
    
    def get_duration_display(self):
        days = self.get_duration_days()
        if days == 0:
            return "0 days"
        elif days < 7:
            return f"{days} day{'s' if days != 1 else ''}"
        elif days < 30:
            weeks = self.get_duration_weeks()
            return f"{weeks} week{'s' if weeks != 1 else ''}"
        elif days < 365:
            months = self.get_duration_months()
            return f"{months} month{'s' if months != 1 else ''}"
        else:
            return f"{days} days"
    
    class Meta:
        ordering = ['-year', '-term_number']
        unique_together = ['school', 'year', 'term_number']
    
    def __str__(self):
        return f"{self.name} ({self.year})"


class Stream(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='streams', null=True, blank=True)
    name = models.CharField(max_length=50)
    code = models.CharField(max_length=10, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['name']
        unique_together = ['school', 'name']
    
    def __str__(self):
        return self.name


class TeacherProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='teacher_profile')
    assigned_stream = models.ForeignKey(Stream, on_delete=models.SET_NULL, null=True, blank=True)
    assigned_form = models.CharField(max_length=10, blank=True, null=True)
    phone_number = models.CharField(max_length=15, blank=True)
    profile_picture = models.ImageField(upload_to='profile_pics/', null=True, blank=True)
    last_activity = models.DateTimeField(auto_now=True)
    is_online = models.BooleanField(default=False)
    has_chosen_stream = models.BooleanField(default=False)
    is_approved = models.BooleanField(default=False)
    is_suspended = models.BooleanField(default=False)
    suspension_reason = models.TextField(blank=True, null=True)
    suspended_at = models.DateTimeField(blank=True, null=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_teachers')
    
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.assigned_stream}"
    
    def is_active_user(self):
        return self.is_approved and not self.is_suspended


class UserSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_key = models.CharField(max_length=40, unique=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    login_time = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-last_activity']
    
    def __str__(self):
        return f"{self.user.username} - {self.login_time}"


class DisciplineCategory(models.Model):
    CATEGORY_KEYS = [
        ('ATTENDANCE', 'Attendance Offenses'),
        ('ACADEMIC', 'Academic Offenses'),
        ('UNIFORM', 'Uniform & Grooming Offenses'),
        ('CLASSROOM', 'Classroom Misconduct'),
        ('DISRESPECT', 'Disrespect & Insubordination'),
        ('BULLYING', 'Bullying & Harassment'),
        ('FIGHTING', 'Fighting & Violence'),
        ('THEFT', 'Theft & Dishonesty'),
        ('PROPERTY', 'School Property Offenses'),
        ('TECHNOLOGY', 'Technology & Phone Misuse'),
        ('SUBSTANCE', 'Substance Abuse Offenses'),
        ('SEXUAL', 'Sexual Misconduct'),
        ('RELATIONSHIP', 'Relationship & Coupling Offenses'),
        ('SECURITY', 'School Security Offenses'),
        ('MASS_INDISCIPLINE', 'Mass Indiscipline'),
        ('HEALTH_SAFETY', 'Health & Safety Violations'),
        ('COMMUNITY', 'Community & Social Misconduct'),
        ('EXAM', 'Examination & Assessment Offenses'),
        ('LEADERSHIP', 'Leadership & Prefect Misconduct'),
        ('TRANSPORT', 'Transport & Travel Offenses'),
        ('ENVIRONMENT', 'Environmental & Sanitation Offenses'),
        ('CRIMINAL', 'Criminal & Legal Offenses'),
    ]
    
    key = models.CharField(max_length=30, choices=CATEGORY_KEYS, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    default_rating = models.CharField(max_length=20, choices=[
        ('VERY_MINOR', 'Very Minor'),
        ('MINOR', 'Minor'),
        ('MODERATE', 'Moderate'),
        ('SERIOUS', 'Serious'),
        ('VERY_SERIOUS', 'Very Serious'),
    ], default='MODERATE')
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)
    
    # AI-specific fields for better risk calculation
    risk_weight = models.PositiveSmallIntegerField(
        default=1,
        choices=[(1, 'Very Low'), (2, 'Low'), (3, 'Medium'), (4, 'High'), (5, 'Very High')],
        help_text="Weight multiplier for risk calculation (1-5)"
    )
    severity_level = models.PositiveSmallIntegerField(
        default=3,
        choices=[(1, 'Very Low'), (2, 'Low'), (3, 'Medium'), (4, 'High'), (5, 'Very High')],
        help_text="Severity level of the offense"
    )
    
    class Meta:
        verbose_name_plural = 'Discipline Categories'
        ordering = ['order', 'name']
    
    def __str__(self):
        return self.name
    
    def get_risk_multiplier(self):
        """Get risk multiplier based on severity level"""
        return self.severity_level


class Student(models.Model):
    FORM_CHOICES = [(f'Form {i}', f'Form {i}') for i in range(1, 11)]
    
    RISK_LEVEL_CHOICES = [
        ('GOOD', 'Good'),
        ('WARNING', 'Warning'),
        ('CRITICAL', 'Critical'),
    ]
    
    ACADEMIC_STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('TRANSFERRED', 'Transferred'),
        ('DROPPED', 'Dropped Out'),
        ('GRADUATED', 'Graduated'),
    ]
    
    admission_number = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=200, db_index=True)
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE, related_name='students')
    grade_level = models.ForeignKey(GradeLevel, on_delete=models.SET_NULL, null=True, blank=True, related_name='students')
    form = models.CharField(max_length=10, choices=FORM_CHOICES)
    year = models.IntegerField(default=timezone.now().year)
    optional_notes = models.TextField(blank=True)
    profile_picture = models.ImageField(upload_to='student_pics/', null=True, blank=True)
    
    # Risk Management Fields
    risk_score = models.IntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    risk_level = models.CharField(
        max_length=20,
        choices=RISK_LEVEL_CHOICES,
        default='GOOD',
        db_index=True
    )
    
    # AI-specific fields for advanced analytics
    ai_risk_factors = models.JSONField(default=dict, blank=True, null=True)
    ai_last_analysis = models.DateTimeField(null=True, blank=True)
    intervention_count = models.PositiveIntegerField(default=0)
    last_incident_date = models.DateTimeField(null=True, blank=True)
    
    # Student Status Fields
    enrollment_date = models.DateField(null=True, blank=True)
    academic_status = models.CharField(
        max_length=20,
        choices=ACADEMIC_STATUS_CHOICES,
        default='ACTIVE'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_students')
    is_active = models.BooleanField(default=True)

    # Soft-delete / archival (data protection & retention)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='deleted_students')
    deletion_reason = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='archived_students')
    archive_reason = models.TextField(blank=True)

    objects = ActiveObjectsManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['admission_number']),
            models.Index(fields=['name']),
            models.Index(fields=['stream', 'form']),
            models.Index(fields=['risk_score']),
            models.Index(fields=['risk_level']),
            models.Index(fields=['last_incident_date']),
            models.Index(fields=['academic_status']),
        ]

    def soft_delete(self, user=None, reason=''):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.deletion_reason = reason
        self.is_active = False
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason', 'is_active', 'updated_at'])

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.deletion_reason = ''
        self.is_active = True
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason', 'is_active', 'updated_at'])

    def archive(self, user=None, reason=''):
        self.is_archived = True
        self.archived_at = timezone.now()
        self.archived_by = user
        self.archive_reason = reason
        self.save(update_fields=['is_archived', 'archived_at', 'archived_by', 'archive_reason', 'updated_at'])

    def unarchive(self):
        self.is_archived = False
        self.archived_at = None
        self.archived_by = None
        self.archive_reason = ''
        self.save(update_fields=['is_archived', 'archived_at', 'archived_by', 'archive_reason', 'updated_at'])
    
    def __str__(self):
        return f"{self.name} ({self.admission_number})"
    
    def update_risk_score(self):
        """Update risk score based on all reports"""
        total = self.reports.aggregate(total=Sum('points'))['total'] or 0
        self.risk_score = min(total, 100)
        self.update_risk_level()
        self.save(update_fields=['risk_score', 'risk_level', 'updated_at'])
        return self.risk_score
    
    def update_risk_level(self):
        """Update risk level based on risk score"""
        if self.risk_score >= 60:
            self.risk_level = 'CRITICAL'
        elif self.risk_score >= 30:
            self.risk_level = 'WARNING'
        else:
            self.risk_level = 'GOOD'
    
    def update_last_incident(self):
        """Update last incident date from latest report"""
        latest_report = self.reports.order_by('-reported_at').first()
        if latest_report:
            self.last_incident_date = latest_report.reported_at
            self.save(update_fields=['last_incident_date'])
    
    def increment_intervention(self):
        """Increment intervention counter"""
        self.intervention_count += 1
        self.save(update_fields=['intervention_count'])
    
    def save(self, *args, **kwargs):
        """Override save to auto-update risk level"""
        self.update_risk_level()
        super().save(*args, **kwargs)
    
    @property
    def is_critical(self):
        return self.risk_score >= 60

    def explain_risk(self):
        """Return a human-readable, fair explanation of the current risk score.

        Explainability matters for legal defensibility: shows the factors that
        drove the score, not just a number.
        """
        reports = self.reports.select_related('category').order_by('-reported_at')
        top_categories = (
            reports.values('category__name')
            .annotate(count=models.Count('id'))
            .order_by('-count')[:3]
        )
        factors = []
        if reports.exists():
            recent = reports[:5]
            factors.append(
                f"{reports.count()} total report(s); most recent "
                f"{self.days_since_last_incident or 'N/A'} day(s) ago"
            )
            for c in top_categories:
                if c['category__name']:
                    factors.append(f"{c['count']}x {c['category__name']}")
        else:
            factors.append("No discipline reports on record")
        trend = self.risk_trend
        trend_label = {
            'improving': 'trending down (improving)',
            'worsening': 'trending up (worsening)',
            'stable': 'stable',
        }.get(trend, 'stable')
        level_reason = {
            'CRITICAL': 'score is 60% or higher — immediate intervention expected',
            'WARNING': 'score is between 30% and 59% — closer monitoring advised',
            'GOOD': 'score is below 30% — behaving within expectations',
        }.get(self.risk_level, '')
        return {
            'risk_score': self.risk_score,
            'risk_level': self.risk_level,
            'level_reason': level_reason,
            'trend': trend,
            'trend_label': trend_label,
            'drivers': factors,
            'interventions_recorded': self.intervention_count,
            'note': 'Score is computed from logged reports and their category weights; '
                    'it is a guide for staff, not a verdict.',
        }
    
    @property
    def total_reports(self):
        return self.reports.count()
    
    @property
    def days_since_last_incident(self):
        """Calculate days since last incident"""
        if self.last_incident_date:
            delta = timezone.now() - self.last_incident_date
            return delta.days
        return None
    
    @property
    def risk_trend(self):
        """Determine risk trend based on recent reports"""
        recent_reports = self.reports.order_by('-reported_at')[:5]
        if recent_reports.count() < 2:
            return 'stable'
        
        # Compare points from oldest to newest
        points = [r.points for r in recent_reports]
        if points[0] > points[-1]:
            return 'improving'
        elif points[0] < points[-1]:
            return 'worsening'
        return 'stable'


class DisciplineReport(models.Model):
    RATING_CHOICES = [
        ('VERY_MINOR', 'Very Minor'),
        ('MINOR', 'Minor'),
        ('MODERATE', 'Moderate'),
        ('SERIOUS', 'Serious'),
        ('VERY_SERIOUS', 'Very Serious'),
    ]
    
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='reports')
    reported_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports_made')
    category = models.ForeignKey(DisciplineCategory, on_delete=models.PROTECT, related_name='reports')
    category_name = models.CharField(max_length=200, editable=False)
    comments = models.TextField(blank=True)
    rating = models.CharField(max_length=20, choices=RATING_CHOICES, default='MODERATE')
    points = models.IntegerField(default=10)
    reported_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Approval workflow (second staff sign-off)
    APPROVAL_STATUS_CHOICES = [
        ('PENDING', 'Pending Review'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    approval_status = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default='PENDING', db_index=True)
    requires_approval = models.BooleanField(default=False, help_text="Set true for critical/serious reports that need a second sign-off")
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reports_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    # Incident-to-action pipeline
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_reports')
    follow_up_date = models.DateField(null=True, blank=True)
    intervention_notes = models.TextField(blank=True)
    is_resolved = models.BooleanField(default=False, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_reports')

    # Soft-delete
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='deleted_reports')
    deletion_reason = models.TextField(blank=True)

    objects = ActiveObjectsManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-reported_at']
        indexes = [
            models.Index(fields=['reported_at']),
            models.Index(fields=['student', 'reported_at']),
            models.Index(fields=['rating']),
            models.Index(fields=['points']),
            models.Index(fields=['approval_status']),
            models.Index(fields=['is_resolved']),
            models.Index(fields=['requires_approval']),
        ]

    def __str__(self):
        return f"{self.student.name} - {self.category_name} by {self.reported_by.username}"

    def delete(self, *args, **kwargs):
        # Soft-delete: never hard-remove a discipline record
        if kwargs.pop('hard', False):
            return super().delete(*args, **kwargs)
        self.is_deleted = True
        self.deleted_at = timezone.now()
        user = kwargs.pop('user', None)
        self.deleted_by = user
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'updated_at'])

    def soft_delete(self, user=None, reason=''):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.deletion_reason = reason
        self.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by', 'deletion_reason', 'updated_at'])

    def approve(self, user, notes=''):
        self.approval_status = 'APPROVED'
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.save(update_fields=['approval_status', 'reviewed_by', 'reviewed_at', 'review_notes', 'updated_at'])

    def reject(self, user, notes=''):
        self.approval_status = 'REJECTED'
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.save(update_fields=['approval_status', 'reviewed_by', 'reviewed_at', 'review_notes', 'updated_at'])

    def resolve(self, user, notes=''):
        self.is_resolved = True
        self.resolved_at = timezone.now()
        self.resolved_by = user
        if notes:
            self.intervention_notes = notes
        self.student.increment_intervention()
        self.save(update_fields=['is_resolved', 'resolved_at', 'resolved_by', 'intervention_notes', 'updated_at'])

    def save(self, *args, **kwargs):
        self.category_name = self.category.name
        rating_points = {
            'VERY_MINOR': 5,
            'MINOR': 10,
            'MODERATE': 20,
            'SERIOUS': 30,
            'VERY_SERIOUS': 40,
        }
        # Apply category risk weight multiplier
        base_points = rating_points.get(self.rating, 10)
        self.points = base_points * self.category.risk_weight
        # Auto-flag serious/critical offences for mandatory second sign-off
        if self.category.risk_weight >= 4 and not self.pk:
            self.requires_approval = True
            self.approval_status = 'PENDING'
        super().save(*args, **kwargs)
        self.student.update_risk_score()
        self.student.update_last_incident()
        self._notify_class_teacher()
        if self.student.total_reports >= 20 or self.student.risk_score >= 60:
            self._notify_admin()
        self._create_ai_analysis()
        self._log_history('CREATE' if not getattr(self, '_history_logged', False) else 'UPDATE')
    
    def _notify_class_teacher(self):
        class_teachers = User.objects.filter(
            teacher_profile__assigned_stream=self.student.stream,
            groups__name='ClassTeacher'
        )
        if class_teachers.exists():
            notification = Notification.objects.create(
                title=f'📝 New Report: {self.student.name}',
                message=(
                    f'Student: {self.student.name} (ID: {self.student.admission_number})\n'
                    f'Category: {self.category_name}\n'
                    f'Points: +{self.points}\n'
                    f'Rating: {self.get_rating_display()}\n'
                    f'Risk Level: {self.student.risk_level}\n'
                    f'Reported by: {self.reported_by.get_full_name() or self.reported_by.username}'
                ),
                notification_type='warning',
                student=self.student,
            )
            notification.target_users.set(class_teachers)
    
    def _notify_admin(self):
        admins = User.objects.filter(is_superuser=True)
        if admins.exists():
            notification = Notification.objects.create(
                title=f'⚠️ CRITICAL ALERT: {self.student.name}',
                message=(
                    f'Student: {self.student.name} (ID: {self.student.admission_number})\n'
                    f'Total Reports: {self.student.total_reports}\n'
                    f'Risk Score: {self.student.risk_score}%\n'
                    f'Risk Level: {self.student.risk_level}\n'
                    f'Last Report: {self.category_name} by '
                    f'{self.reported_by.get_full_name() or self.reported_by.username}\n'
                    f'Days since last incident: {self.student.days_since_last_incident or "N/A"}'
                ),
                notification_type='critical',
                student=self.student,
            )
            notification.target_users.set(admins)
            notification.save()
    
    def _create_ai_analysis(self):
        """Create AI analysis for the student"""
        from django.utils import timezone
        import json
        
        student = self.student
        analysis = {
            'last_analysis': timezone.now().isoformat(),
            'total_reports': student.total_reports,
            'risk_score': student.risk_score,
            'risk_level': student.risk_level,
            'recent_trend': student.risk_trend,
            'days_since_incident': student.days_since_last_incident,
            'intervention_count': student.intervention_count,
            'category_breakdown': list(
                student.reports.values('category__name')
                .annotate(count=models.Count('id'))
                .order_by('-count')
            )
        }
        student.ai_risk_factors = analysis
        student.ai_last_analysis = timezone.now()
        student.save(update_fields=['ai_risk_factors', 'ai_last_analysis'])

    def _log_history(self, action):
        """Append an immutable audit entry for this report."""
        ReportHistory.objects.create(
            report=self,
            action=action,
            changed_by=self.reported_by if action == 'CREATE' else None,
            snapshot={
                'student': self.student.admission_number,
                'category': self.category_name,
                'rating': self.rating,
                'points': self.points,
                'comments': self.comments,
                'approval_status': self.approval_status,
                'is_resolved': self.is_resolved,
            },
        )
        self._history_logged = True


class ReportHistory(models.Model):
    """Immutable audit trail for every discipline report mutation."""
    ACTION_CHOICES = [
        ('CREATE', 'Created'),
        ('UPDATE', 'Updated'),
        ('APPROVE', 'Approved'),
        ('REJECT', 'Rejected'),
        ('RESOLVE', 'Resolved'),
        ('DELETE', 'Deleted (soft)'),
        ('RESTORE', 'Restored'),
    ]

    report = models.ForeignKey(DisciplineReport, on_delete=models.CASCADE, related_name='history')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='report_changes')
    changed_at = models.DateTimeField(auto_now_add=True)
    snapshot = models.JSONField(default=dict, blank=True, help_text="Field values at time of change")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-changed_at']
        indexes = [
            models.Index(fields=['report', 'changed_at']),
            models.Index(fields=['action']),
        ]

    def __str__(self):
        return f"{self.report_id} - {self.action} @ {self.changed_at:%Y-%m-%d %H:%M}"


class Sanction(models.Model):
    """Consequence (detention, community service, etc.) linked to a report."""
    TYPE_CHOICES = [
        ('DETENTION', 'Detention'),
        ('COMMUNITY_SERVICE', 'Community Service'),
        ('SUSPENSION', 'Suspension'),
        ('COUNSELING', 'Counseling'),
        ('PARENT_MEETING', 'Parent Meeting'),
        ('WARNING_LETTER', 'Warning Letter'),
        ('OTHER', 'Other'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SCHEDULED', 'Scheduled'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('WAIVED', 'Waived'),
    ]

    report = models.ForeignKey(DisciplineReport, on_delete=models.CASCADE, related_name='sanctions')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='sanctions')
    sanction_type = models.CharField(max_length=25, choices=TYPE_CHOICES)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_sanctions')
    scheduled_date = models.DateField(null=True, blank=True)
    completed_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_sanctions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['student', 'status']),
            models.Index(fields=['scheduled_date']),
        ]

    def __str__(self):
        return f"{self.student.name} - {self.get_sanction_type_display()} ({self.status})"


class Appeal(models.Model):
    """Student/parent dispute of a discipline report."""
    STATUS_CHOICES = [
        ('OPEN', 'Open'),
        ('UNDER_REVIEW', 'Under Review'),
        ('UPHELD', 'Upheld (report stands)'),
        ('Overturned', 'Overturned (report reversed)'),
        ('PARTIALLY_UPHELD', 'Partially Upheld'),
        ('CLOSED', 'Closed'),
    ]

    report = models.ForeignKey(DisciplineReport, on_delete=models.CASCADE, related_name='appeals')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='appeals')
    filed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='filed_appeals')
    filed_by_role = models.CharField(max_length=20, blank=True, help_text="STUDENT / PARENT / GUARDIAN")
    reason = models.TextField(help_text="Why the report is being disputed")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_appeals')
    review_notes = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['student', 'status']),
        ]

    def __str__(self):
        return f"Appeal #{self.id} - {self.student.name} ({self.status})"

    def review(self, user, status, notes=''):
        self.status = status
        self.reviewed_by = user
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes', 'updated_at'])
        if status == 'Overturned':
            self.report.soft_delete(user=user, reason=f"Overturned via appeal #{self.id}")


class CommunicationLog(models.Model):
    """Record of every outbound parent/guardian communication (consent & audit)."""
    CHANNEL_CHOICES = [
        ('EMAIL', 'Email'),
        ('SMS', 'SMS'),
        ('PUSH', 'Push Notification'),
        ('LETTER', 'Physical Letter'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='communications')
    parent = models.ForeignKey('ParentProfile', on_delete=models.SET_NULL, null=True, blank=True, related_name='communications')
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    subject = models.CharField(max_length=200)
    body = models.TextField()
    report = models.ForeignKey(DisciplineReport, on_delete=models.SET_NULL, null=True, blank=True, related_name='communications')
    sent_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_communications')
    consent_given = models.BooleanField(default=False, help_text="Parent opted in to this channel")
    status = models.CharField(max_length=20, default='SENT', choices=[('SENT', 'Sent'), ('FAILED', 'Failed'), ('BOUNCED', 'Bounced')])
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['student', 'sent_at']),
            models.Index(fields=['channel']),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} -> {self.student.name}: {self.subject}"


class RateLimitLog(models.Model):
    """Per-teacher / per-student daily report throttle & anomaly tracking."""
    reported_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rate_logs')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='rate_logs')
    reported_at = models.DateTimeField(auto_now_add=True)
    flagged = models.BooleanField(default=False, help_text="True if this hit an anomaly threshold")

    class Meta:
        indexes = [
            models.Index(fields=['reported_by', 'reported_at']),
            models.Index(fields=['student', 'reported_at']),
        ]

    def __str__(self):
        return f"{self.reported_by.username} -> {self.student.name} @ {self.reported_at:%Y-%m-%d}"


class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('critical', 'Critical Alert'),
        ('warning', 'Warning'),
        ('info', 'Information'),
        ('success', 'Success'),
    ]
    
    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_read = models.BooleanField(default=False)
    target_users = models.ManyToManyField(User, related_name='notifications')
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['is_read']),
            models.Index(fields=['notification_type']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.notification_type}"
    
    def mark_read(self, user):
        self.target_users.remove(user)
        if not self.target_users.exists():
            self.is_read = True
            self.save(update_fields=['is_read'])


class PasswordReset(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('completed', 'Completed'),
        ('rejected', 'Rejected'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_resets')
    requested_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_resets')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    new_password = models.CharField(max_length=128, blank=True, null=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['requested_at']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.status} - {self.requested_at}"


# ============================================
# HELPER FUNCTIONS
# ============================================

def create_admin_notification(title, message, notification_type='info', student=None):
    """Create a notification for all admin users"""
    admins = User.objects.filter(is_superuser=True)
    if admins.exists():
        notification = Notification.objects.create(
            title=title,
            message=message,
            notification_type=notification_type,
            student=student,
        )
        notification.target_users.set(admins)
        return notification
    return None


def create_user_notification(user, title, message, notification_type='info', student=None):
    """Create a notification for a specific user"""
    notification = Notification.objects.create(
        title=title,
        message=message,
        notification_type=notification_type,
        student=student,
    )
    notification.target_users.add(user)
    if notification_type == 'critical':
        create_admin_notification(f'⚠️ {title}', message, 'critical', student)
    return notification


def bulk_update_risk_levels():
    """Utility function to update all students' risk levels"""
    from django.db import transaction
    
    with transaction.atomic():
        for student in Student.objects.all():
            student.update_risk_level()
            student.save(update_fields=['risk_level'])
    print(f"✅ Updated risk levels for {Student.objects.count()} students")


def calculate_risk_trend(student):
    """Calculate risk trend for a student"""
    recent_reports = student.reports.order_by('-reported_at')[:5]
    if recent_reports.count() < 2:
        return 'stable'
    
    points = [r.points for r in recent_reports]
    if points[0] > points[-1]:
        return 'improving'
    elif points[0] < points[-1]:
        return 'worsening'
    return 'stable'


# ============================================
# SUBJECT MODEL
# ============================================

class Subject(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='subjects')
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['name']
        unique_together = ['school', 'code']
    
    def __str__(self):
        return self.name


# ============================================
# TIMETABLE ENTRY MODEL
# ============================================

class TimetableEntry(models.Model):
    DAY_CHOICES = [
        ('MONDAY', 'Monday'),
        ('TUESDAY', 'Tuesday'),
        ('WEDNESDAY', 'Wednesday'),
        ('THURSDAY', 'Thursday'),
        ('FRIDAY', 'Friday'),
        ('SATURDAY', 'Saturday'),
    ]
    
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='timetable_entries')
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE, related_name='timetable_entries')
    form = models.CharField(max_length=10)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='timetable_entries')
    teacher = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='timetable_entries')
    day = models.CharField(max_length=10, choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['day', 'start_time']
        indexes = [
            models.Index(fields=['stream', 'form', 'day']),
            models.Index(fields=['teacher', 'day', 'start_time']),
        ]
    
    def __str__(self):
        return f"{self.stream.name} - {self.form} - {self.subject.name} ({self.day})"
    
    def clean(self):
        from django.core.exceptions import ValidationError
        if self.start_time >= self.end_time:
            raise ValidationError('Start time must be before end time.')
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        self._check_conflicts()
    
    def _check_conflicts(self):
        conflicts = TimetableEntry.objects.filter(
            school=self.school,
            stream=self.stream,
            form=self.form,
            day=self.day,
            is_active=True
        ).exclude(id=self.id)
        
        for conflict in conflicts:
            if (self.start_time < conflict.end_time and self.end_time > conflict.start_time):
                pass


# ============================================
# ATTENDANCE RECORD MODEL
# ============================================

class AttendanceRecord(models.Model):
    STATUS_CHOICES = [
        ('PRESENT', 'Present'),
        ('ABSENT', 'Absent'),
        ('LATE', 'Late'),
        ('EXCUSED', 'Excused'),
        ('LEAVE', 'On Leave'),
    ]
    
    CHECKIN_METHOD_CHOICES = [
        ('QR_CODE', 'QR Code'),
        ('NFC', 'NFC Card'),
        ('MANUAL', 'Manual Entry'),
        ('BIOMETRIC', 'Biometric'),
    ]
    
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='attendance_records')
    stream = models.ForeignKey(Stream, on_delete=models.CASCADE, related_name='attendance_records')
    form = models.CharField(max_length=10)
    date = models.DateField()
    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PRESENT')
    checkin_method = models.CharField(max_length=20, choices=CHECKIN_METHOD_CHOICES, default='MANUAL')
    qr_code_data = models.CharField(max_length=200, blank=True)
    remarks = models.TextField(blank=True)
    marked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance_marked')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-date', '-check_in_time']
        unique_together = ['student', 'date']
        indexes = [
            models.Index(fields=['student', 'date']),
            models.Index(fields=['stream', 'date']),
            models.Index(fields=['status', 'date']),
        ]
    
    def __str__(self):
        return f"{self.student.name} - {self.date} - {self.status}"


# ============================================
# PARENT PROFILE MODEL
# ============================================

class ParentProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='parent_profile')
    phone_number = models.CharField(max_length=15, blank=True)
    email = models.EmailField(blank=True)
    national_id = models.CharField(max_length=30, blank=True)
    occupation = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=15, blank=True)
    receive_sms = models.BooleanField(default=True)
    receive_email = models.BooleanField(default=True)
    receive_push = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['user__username']
    
    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - Parent"


class StudentParentLink(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='parent_links')
    parent = models.ForeignKey(ParentProfile, on_delete=models.CASCADE, related_name='student_links')
    relationship = models.CharField(max_length=50, default='Parent/Guardian')
    is_primary = models.BooleanField(default=False)
    can_pickup = models.BooleanField(default=True)
    receive_notifications = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['student', 'parent']
        ordering = ['-is_primary']
    
    def __str__(self):
        return f"{self.student.name} - {self.parent.user.get_full_name()}"


# ============================================
# GAMIFICATION - STUDENT POINTS
# ============================================

class StudentPoints(models.Model):
    REASON_CHOICES = [
        ('DISCIPLINE_REPORT', 'Discipline Report'),
        ('POSITIVE_BEHAVIOR', 'Positive Behavior'),
        ('INTERVENTION_SUCCESS', 'Intervention Success'),
        ('PERFECT_ATTENDANCE', 'Perfect Attendance'),
        ('ACADEMIC_EXCELLENCE', 'Academic Excellence'),
        ('LEADERSHIP', 'Leadership'),
        ('COMMUNITY_SERVICE', 'Community Service'),
        ('MANUAL_ADJUSTMENT', 'Manual Adjustment'),
        ('PENALTY', 'Penalty Deduction'),
    ]
    
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='points_history')
    points = models.IntegerField()
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    description = models.TextField(blank=True)
    related_report = models.ForeignKey(DisciplineReport, on_delete=models.SET_NULL, null=True, blank=True)
    awarded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='points_awarded')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['student', 'created_at']),
            models.Index(fields=['reason', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.student.name}: {self.points:+.0f} pts ({self.reason})"


# ============================================
# GAMIFICATION - REWARDS
# ============================================

class Reward(models.Model):
    REWARD_TYPE_CHOICES = [
        ('BADGE', 'Badge'),
        ('CERTIFICATE', 'Certificate'),
        ('PRIZE', 'Prize'),
        ('PRIVILEGE', 'Privilege'),
        ('RECOGNITION', 'Public Recognition'),
        ('OTHER', 'Other'),
    ]
    
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='rewards')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    reward_type = models.CharField(max_length=20, choices=REWARD_TYPE_CHOICES)
    icon = models.CharField(max_length=50, blank=True, help_text="Font Awesome icon class, e.g. fa-trophy")
    color = models.CharField(max_length=20, default='#f59e0b', help_text="Hex color code")
    points_required = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['points_required', 'name']
    
    def __str__(self):
        return self.name


class StudentReward(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='rewards_earned')
    reward = models.ForeignKey(Reward, on_delete=models.CASCADE, related_name='student_earnings')
    awarded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rewards_awarded')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        unique_together = ['student', 'reward', 'created_at']
    
    def __str__(self):
        return f"{self.student.name} earned {self.reward.name}"


# ============================================
# GAMIFICATION - LEADERBOARD
# ============================================

class LeaderboardEntry(models.Model):
    PERIOD_CHOICES = [
        ('WEEKLY', 'Weekly'),
        ('MONTHLY', 'Monthly'),
        ('TERMLY', 'Termly'),
        ('YEARLY', 'Yearly'),
        ('ALL_TIME', 'All Time'),
    ]
    
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='leaderboard_entries')
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name='leaderboard_entries')
    period = models.CharField(max_length=20, choices=PERIOD_CHOICES)
    period_start = models.DateField()
    period_end = models.DateField()
    total_points = models.IntegerField(default=0)
    rank = models.PositiveIntegerField(default=0)
    points_delta = models.IntegerField(default=0, help_text="Change from previous period")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-total_points', 'rank']
        unique_together = ['student', 'period', 'period_start', 'period_end']
        indexes = [
            models.Index(fields=['school', 'period', '-total_points']),
        ]
    
    def __str__(self):
        return f"{self.student.name} - {self.period} - Rank #{self.rank}"


# ============================================
# DOCUMENT VAULT MODEL
# ============================================

class DocumentVault(models.Model):
    DOCUMENT_TYPE_CHOICES = [
        ('REPORT_CARD', 'Report Card'),
        ('TRANSFER_LETTER', 'Transfer Letter'),
        ('MEDICAL_RECORD', 'Medical Record'),
        ('BIRTH_CERTIFICATE', 'Birth Certificate'),
        ('PHOTO_ID', 'Photo ID'),
        ('PARENT_ID', 'Parent/Guardian ID'),
        ('DISCIPLINE_RECORD', 'Discipline Record'),
        ('ACADEMIC_TRANSCRIPT', 'Academic Transcript'),
        ('AWARD_CERTIFICATE', 'Award Certificate'),
        ('OTHER', 'Other'),
    ]
    
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='documents')
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPE_CHOICES)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to='documents/%Y/%m/%d/')
    file_size = models.PositiveIntegerField(default=0)
    mime_type = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    is_confidential = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_documents')
    version = models.PositiveIntegerField(default=1)
    previous_version = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='newer_versions')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['student', 'document_type']),
            models.Index(fields=['is_confidential', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.student.name} - {self.title}"
    
    def save(self, *args, **kwargs):
        if self.file:
            self.file_size = self.file.size
            self.mime_type = getattr(self.file, 'content_type', '')
        super().save(*args, **kwargs)


# ============================================
# USER ROLE MODEL
# ============================================

class UserRole(models.Model):
    ROLE_CHOICES = [
        ('ADMIN', 'Administrator'),
        ('CLASS_TEACHER', 'Class Teacher'),
        ('TEACHER', 'Teacher'),
        ('STUDENT', 'Student'),
        ('PARENT', 'Parent'),
        ('COUNSELOR', 'Counselor'),
        ('LIBRARIAN', 'Librarian'),
        ('NURSE', 'School Nurse'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='role_profile')
    role = models.CharField(max_length=30, choices=ROLE_CHOICES)
    permissions = models.JSONField(default=dict, blank=True, help_text="Custom permissions JSON")
    department = models.CharField(max_length=100, blank=True)
    employee_id = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['role', 'user__username']
        verbose_name_plural = 'User Roles'
    
    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.get_role_display()}"


# ============================================
# ENHANCED HELPER FUNCTIONS
# ============================================

def create_admin_notification(title, message, notification_type='info', student=None):
    admins = User.objects.filter(is_superuser=True)
    if admins.exists():
        notification = Notification.objects.create(
            title=title,
            message=message,
            notification_type=notification_type,
            student=student,
        )
        notification.target_users.set(admins)
        return notification
    return None


def create_user_notification(user, title, message, notification_type='info', student=None):
    notification = Notification.objects.create(
        title=title,
        message=message,
        notification_type=notification_type,
        student=student,
    )
    notification.target_users.add(user)
    if notification_type == 'critical':
        create_admin_notification(f'Critical: {title}', message, 'critical', student)
    return notification


def bulk_update_risk_levels():
    with transaction.atomic():
        for student in Student.objects.all():
            student.update_risk_level()
            student.save(update_fields=['risk_level'])
    print(f"Updated risk levels for {Student.objects.count()} students")


def calculate_risk_trend(student):
    recent_reports = student.reports.order_by('-reported_at')[:5]
    if recent_reports.count() < 2:
        return 'stable'
    
    points = [r.points for r in recent_reports]
    if points[0] > points[-1]:
        return 'improving'
    elif points[0] < points[-1]:
        return 'worsening'
    return 'stable'


def get_student_leaderboard(stream=None, period='MONTHLY', limit=10):
    school = School.objects.first()
    if not school:
        return []
    
    now = timezone.now()
    if period == 'WEEKLY':
        period_start = now - timedelta(days=7)
        period_end = now
    elif period == 'MONTHLY':
        period_start = now - timedelta(days=30)
        period_end = now
    elif period == 'TERMLY':
        period_start = now - timedelta(days=90)
        period_end = now
    elif period == 'YEARLY':
        period_start = now - timedelta(days=365)
        period_end = now
    else:
        period_start = None
        period_end = None
    
    qs = Student.objects.filter(is_active=True)
    if stream:
        qs = qs.filter(stream=stream)
    
    students = []
    for student in qs:
        points = student.points_history.all()
        if period_start and period_end:
            points = points.filter(created_at__date__range=(period_start.date(), period_end.date()))
        total = sum(p.points for p in points)
        students.append({
            'student': student,
            'total_points': total,
            'rewards_count': student.rewards_earned.count(),
        })
    
    students.sort(key=lambda x: x['total_points'], reverse=True)
    return students[:limit]


def get_attendance_stats(stream=None, form=None, date_from=None, date_to=None):
    qs = AttendanceRecord.objects.all()
    if stream:
        qs = qs.filter(stream=stream)
    if form:
        qs = qs.filter(form=form)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    
    total = qs.count()
    present = qs.filter(status='PRESENT').count()
    absent = qs.filter(status='ABSENT').count()
    late = qs.filter(status='LATE').count()
    excused = qs.filter(status='EXCUSED').count()
    
    return {
        'total': total,
        'present': present,
        'absent': absent,
        'late': late,
        'excused': excused,
        'attendance_rate': round((present / total * 100), 1) if total > 0 else 0,
    }
