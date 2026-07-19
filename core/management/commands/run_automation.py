"""
Management command to run automation tasks.
Usage: python manage.py run_automation [daily|weekly|maintenance]
"""
from django.core.management.base import BaseCommand
from core.automation import AutomationRunner


class Command(BaseCommand):
    help = 'Run automation tasks (daily, weekly, maintenance)'

    def add_arguments(self, parser):
        parser.add_argument(
            'task_type',
            type=str,
            choices=['daily', 'weekly', 'maintenance', 'all'],
            default='daily',
            help='Type of automation task to run'
        )

    def handle(self, *args, **options):
        task_type = options['task_type']
        runner = AutomationRunner()
        
        self.stdout.write(f'Running {task_type} automation tasks...')
        
        if task_type == 'daily' or task_type == 'all':
            result = runner.run_daily_tasks()
            if result.get('success'):
                self.stdout.write(self.style.SUCCESS('Daily tasks completed successfully'))
                for task_name, task_result in result.get('results', {}).items():
                    status = 'OK' if task_result.get('success') else f'FAILED: {task_result.get("error")}'
                    self.stdout.write(f'  {task_name}: {status}')
            else:
                self.stdout.write(self.style.ERROR(f'Daily tasks failed: {result.get("error")}'))
        
        if task_type == 'weekly' or task_type == 'all':
            result = runner.run_weekly_tasks()
            if result.get('success'):
                self.stdout.write(self.style.SUCCESS('Weekly tasks completed successfully'))
            else:
                self.stdout.write(self.style.ERROR(f'Weekly tasks failed: {result.get("error")}'))
        
        if task_type == 'maintenance' or task_type == 'all':
            result = runner.run_maintenance_tasks()
            if result.get('success'):
                self.stdout.write(self.style.SUCCESS('Maintenance tasks completed successfully'))
            else:
                self.stdout.write(self.style.ERROR(f'Maintenance tasks failed: {result.get("error")}'))
