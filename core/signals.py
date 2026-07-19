from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User, Group
from .models import TeacherProfile

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Create teacher profile for every new user"""
    if created:
        TeacherProfile.objects.get_or_create(user=instance)

@receiver(post_save, sender=User)
def assign_default_group(sender, instance, created, **kwargs):
    """Assign default group for new users"""
    if created and not instance.is_superuser:
        if not instance.groups.exists():
            teacher_group, _ = Group.objects.get_or_create(name='Teacher')
            instance.groups.add(teacher_group)
