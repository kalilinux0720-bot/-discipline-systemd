"""
Aether AI Integration Module
Handles AI-powered student queries, report generation, and analytics.
Uses Pollination AI API (gpt-4o) for intelligent responses.
"""
import json
import os
import logging
import requests
from django.conf import settings
from django.utils import timezone
from django.db.models import Count, Avg, Sum, Q, Max, Min
from datetime import timedelta
from django.contrib.auth.models import User

from .models import (
    Student, DisciplineReport, DisciplineCategory,
    Notification, Stream, TeacherProfile, School,
    AttendanceRecord, StudentPoints, Reward,
)

logger = logging.getLogger(__name__)


# ============================================
# AETHER AI CLIENT
# ============================================

class AetherAI:
    """Main AI client for Aether integration"""
    
    def __init__(self):
        self.api_key = os.environ.get('AETHER_API_KEY', '')
        self.base_url = os.environ.get('AETHER_API_URL', 'https://api.pollinations.ai/v1')
        self.model = os.environ.get('AETHER_MODEL', 'gpt-4o')
        self.timeout = int(os.environ.get('AETHER_TIMEOUT', '60'))
        self.max_tokens = int(os.environ.get('AETHER_MAX_TOKENS', '2000'))
        self.temperature = float(os.environ.get('AETHER_TEMPERATURE', '0.7'))
    
    def _get_headers(self):
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
    
    def chat(self, user_message, student_id=None, conversation_history=None):
        if not self.api_key:
            return {'success': False, 'error': 'Aether API key not configured. Set AETHER_API_KEY environment variable.'}
        
        try:
            system_prompt = self._build_system_prompt(student_id)
            messages = [{'role': 'system', 'content': system_prompt}]
            
            if conversation_history:
                for msg in conversation_history[-10:]:
                    role = 'user' if msg.get('sender') == 'user' else 'assistant'
                    messages.append({'role': role, 'content': msg.get('text', '')})
            
            messages.append({'role': 'user', 'content': user_message})
            
            payload = {
                'model': self.model,
                'messages': messages,
                'max_tokens': self.max_tokens,
                'temperature': self.temperature,
                'stream': False,
            }
            
            response = requests.post(
                f'{self.base_url}/chat/completions',
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout,
            )
            
            if response.status_code == 200:
                data = response.json()
                ai_message = data['choices'][0]['message']['content']
                usage = data.get('usage', {})
                return {
                    'success': True,
                    'response': ai_message,
                    'usage': usage,
                    'model': self.model,
                }
            else:
                return {
                    'success': False,
                    'error': f'API returned {response.status_code}: {response.text[:200]}',
                }
        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'Request timed out. Please try again.'}
        except requests.exceptions.RequestException as e:
            return {'success': False, 'error': f'Network error: {str(e)}'}
        except Exception as e:
            return {'success': False, 'error': f'Unexpected error: {str(e)}'}
    
    def _build_system_prompt(self, student_id=None):
        school = School.objects.first()
        school_name = school.name if school else 'Unknown School'
        total_students = Student.objects.filter(is_active=True).count()
        critical = Student.objects.filter(is_active=True, risk_level='CRITICAL').count()
        
        prompt = f"""You are Aether, an intelligent AI assistant for {school_name}'s Disciplinary Management System.
You help teachers, administrators, and staff with:
- Student behavior analysis and risk assessment
- Discipline report guidance and recommendations
- Attendance tracking insights
- Gamification strategies
- General school management queries

Current school stats:
- Total students: {total_students}
- Critical risk students: {critical}

Be professional, concise, and helpful. When discussing student data, be factual and objective.
Always suggest evidence-based interventions. Never share sensitive student information inappropriately."""
        
        if student_id:
            try:
                student = Student.objects.get(id=student_id, is_active=True)
                reports = student.reports.select_related('category').order_by('-reported_at')[:10]
                prompt += f"\n\nCurrent student context: {student.name} (ID: {student.admission_number}), Risk: {student.risk_score}% ({student.risk_level}), Reports: {student.total_reports}"
                if reports:
                    categories = [r.category.name for r in reports[:5]]
                    prompt += f", Recent categories: {', '.join(categories)}"
            except Student.DoesNotExist:
                pass
        
        return prompt
    
    def analyze_student_behavior(self, student_id):
        try:
            student = Student.objects.get(id=student_id, is_active=True)
            reports = student.reports.select_related('category').order_by('-reported_at')
            total_reports = reports.count()
            
            if total_reports == 0:
                return {
                    'success': True,
                    'analysis': f"{student.name} has a clean disciplinary record. No reports found.",
                    'risk_assessment': 'GOOD',
                    'recommendations': ['Continue positive reinforcement', 'Maintain regular monitoring'],
                }
            
            category_breakdown = reports.values('category__name').annotate(count=Count('id')).order_by('-count')[:5]
            recent_points = [r.points for r in reports[:5]]
            avg_points = sum(recent_points) / len(recent_points) if recent_points else 0
            
            prompt = f"""Analyze the disciplinary data for student {student.name}:
- Risk Score: {student.risk_score}/100 ({student.risk_level})
- Total Reports: {total_reports}
- Interventions: {student.intervention_count}
- Average Report Points: {avg_points:.1f}
- Risk Trend: {student.risk_trend}

Category Breakdown:
{chr(10).join([f"- {c['category__name']}: {c['count']} reports" for c in category_breakdown])}

Provide:
1. Risk Assessment (1-2 sentences)
2. Key Behavioral Patterns (3 bullet points)
3. Recommended Interventions (3 specific actions)
4. Early Warning Signs (2-3 indicators to watch)

Format as JSON with keys: assessment, patterns, recommendations, warnings"""
            
            messages = [
                {'role': 'system', 'content': self._build_system_prompt(student_id)},
                {'role': 'user', 'content': prompt},
            ]
            
            payload = {
                'model': self.model,
                'messages': messages,
                'max_tokens': self.max_tokens,
                'temperature': 0.3,
                'response_format': {'type': 'json_object'},
            }
            
            response = requests.post(
                f'{self.base_url}/chat/completions',
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout,
            )
            
            if response.status_code == 200:
                data = response.json()
                content = data['choices'][0]['message']['content']
                try:
                    result = json.loads(content)
                    return {
                        'success': True,
                        'analysis': result,
                        'student_id': student_id,
                        'student_name': student.name,
                    }
                except json.JSONDecodeError:
                    return {
                        'success': True,
                        'analysis': {'raw': content},
                        'student_id': student_id,
                        'student_name': student.name,
                    }
            else:
                return {'success': False, 'error': f'API error: {response.status_code}'}
        except Student.DoesNotExist:
            return {'success': False, 'error': 'Student not found'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def generate_report_summary(self, stream_id=None, date_from=None, date_to=None):
        try:
            reports = DisciplineReport.objects.select_related('student', 'category', 'reported_by')
            if stream_id:
                reports = reports.filter(student__stream_id=stream_id)
            if date_from:
                reports = reports.filter(reported_at__date__gte=date_from)
            if date_to:
                reports = reports.filter(reported_at__date__lte=date_to)
            
            total = reports.count()
            if total == 0:
                return {'success': True, 'summary': 'No reports found for the selected period.'}
            
            category_breakdown = reports.values('category__name').annotate(count=Count('id')).order_by('-count')[:5]
            avg_points = reports.aggregate(avg=Avg('points'))['avg'] or 0
            critical_students = reports.filter(student__risk_level='CRITICAL').values('student__name').annotate(count=Count('id')).order_by('-count')[:5]
            
            prompt = f"""Generate a concise disciplinary report summary:

Period: {date_from or 'All time'} to {date_to or 'Present'}
Total Reports: {total}
Average Points: {avg_points:.1f}

Top Categories:
{chr(10).join([f"- {c['category__name']}: {c['count']}" for c in category_breakdown])}

Top Students with Reports:
{chr(10).join([f"- {s['student__name']}: {s['count']} reports" for s in critical_students])}

Write a 3-paragraph professional summary suitable for school administration."""
            
            messages = [
                {'role': 'system', 'content': 'You are Aether, a professional school analytics assistant. Write concise, data-driven summaries.'},
                {'role': 'user', 'content': prompt},
            ]
            
            payload = {
                'model': self.model,
                'messages': messages,
                'max_tokens': self.max_tokens,
                'temperature': 0.5,
            }
            
            response = requests.post(
                f'{self.base_url}/chat/completions',
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout,
            )
            
            if response.status_code == 200:
                data = response.json()
                summary = data['choices'][0]['message']['content']
                return {'success': True, 'summary': summary, 'total_reports': total}
            else:
                return {'success': False, 'error': f'API error: {response.status_code}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_predictive_insights(self):
        try:
            students = Student.objects.filter(is_active=True)
            predictions = []
            
            for student in students:
                reports = student.reports.order_by('reported_at')
                total_reports = reports.count()
                if total_reports == 0:
                    continue
                
                risk_score = student.risk_score
                risk_trend = student.risk_trend
                days_since = student.days_since_last_incident
                
                predicted_risk = risk_score
                if risk_trend == 'worsening':
                    predicted_risk = min(100, predicted_risk + 15)
                elif risk_trend == 'improving' and student.intervention_count > 0:
                    predicted_risk = max(0, predicted_risk - 10)
                
                if days_since and days_since < 7 and risk_score >= 30:
                    predicted_risk = min(100, predicted_risk + 5)
                
                predictions.append({
                    'student_id': student.id,
                    'name': student.name,
                    'stream': student.stream.name if student.stream else 'N/A',
                    'current_risk': risk_score,
                    'predicted_risk': round(predicted_risk, 1),
                    'trend': risk_trend,
                    'interventions': student.intervention_count,
                    'urgency': 'HIGH' if predicted_risk >= 60 else 'MEDIUM' if predicted_risk >= 30 else 'LOW',
                })
            
            predictions.sort(key=lambda x: 0 if x['urgency'] == 'HIGH' else 1 if x['urgency'] == 'MEDIUM' else 2)
            return {'success': True, 'predictions': predictions[:20]}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def suggest_interventions(self, student_id):
        try:
            student = Student.objects.get(id=student_id, is_active=True)
            reports = student.reports.select_related('category').order_by('-reported_at')[:10]
            
            category_patterns = reports.values('category__name').annotate(count=Count('id')).filter(count__gte=2).order_by('-count')
            
            prompt = f"""Suggest 3-5 specific, actionable interventions for student {student.name}:

Current Status:
- Risk Score: {student.risk_score}/100
- Risk Level: {student.risk_level}
- Total Reports: {student.total_reports}
- Interventions Applied: {student.intervention_count}
- Trend: {student.risk_trend}

Repeat Offense Categories:
{chr(10).join([f"- {c['category__name']}: {c['count']} times" for c in category_patterns]) if category_patterns else "No dominant patterns"}

Provide specific, school-appropriate interventions. Be practical and actionable."""
            
            messages = [
                {'role': 'system', 'content': self._build_system_prompt(student_id)},
                {'role': 'user', 'content': prompt},
            ]
            
            payload = {
                'model': self.model,
                'messages': messages,
                'max_tokens': 800,
                'temperature': 0.4,
            }
            
            response = requests.post(
                f'{self.base_url}/chat/completions',
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout,
            )
            
            if response.status_code == 200:
                data = response.json()
                suggestions = data['choices'][0]['message']['content']
                return {
                    'success': True,
                    'student_name': student.name,
                    'suggestions': suggestions,
                    'patterns': list(category_patterns),
                }
            else:
                return {'success': False, 'error': f'API error: {response.status_code}'}
        except Exception as e:
            return {'success': False, 'error': str(e)}


# ============================================
# AETHER ANALYTICS ENGINE
# ============================================

class AetherAnalytics:
    """Advanced analytics using Aether AI"""
    
    def __init__(self):
        self.ai = AetherAI()
    
    def get_school_health_report(self):
        try:
            students = Student.objects.filter(is_active=True)
            total = students.count()
            critical = students.filter(risk_level='CRITICAL').count()
            warning = students.filter(risk_level='WARNING').count()
            good = students.filter(risk_level='GOOD').count()
            
            total_reports = DisciplineReport.objects.count()
            reports_today = DisciplineReport.objects.filter(reported_at__date=timezone.now().date()).count()
            
            categories = DisciplineCategory.objects.filter(is_active=True)
            top_offense = DisciplineReport.objects.values('category__name').annotate(count=Count('id')).order_by('-count').first()
            
            report = {
                'success': True,
                'school_health': {
                    'total_students': total,
                    'good_count': good,
                    'warning_count': warning,
                    'critical_count': critical,
                    'good_percentage': round(good / total * 100, 1) if total else 0,
                    'warning_percentage': round(warning / total * 100, 1) if total else 0,
                    'critical_percentage': round(critical / total * 100, 1) if total else 0,
                    'total_reports': total_reports,
                    'reports_today': reports_today,
                    'top_offense': top_offense['category__name'] if top_offense else 'N/A',
                    'top_offense_count': top_offense['count'] if top_offense else 0,
                    'timestamp': timezone.now().isoformat(),
                }
            }
            return report
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_stream_comparison(self):
        try:
            streams = Stream.objects.filter(is_active=True)
            comparison = []
            
            for stream in streams:
                students = Student.objects.filter(stream=stream, is_active=True)
                total = students.count()
                if total == 0:
                    continue
                
                avg_risk = students.aggregate(avg=Avg('risk_score'))['avg'] or 0
                critical = students.filter(risk_level='CRITICAL').count()
                warning = students.filter(risk_level='WARNING').count()
                total_reports = DisciplineReport.objects.filter(student__stream=stream).count()
                
                comparison.append({
                    'stream_name': stream.name,
                    'total_students': total,
                    'avg_risk_score': round(avg_risk, 1),
                    'critical_count': critical,
                    'warning_count': warning,
                    'total_reports': total_reports,
                    'health_score': round(max(0, 100 - avg_risk), 1),
                })
            
            comparison.sort(key=lambda x: x['health_score'], reverse=True)
            return {'success': True, 'streams': comparison}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_weekly_trends(self):
        try:
            end_date = timezone.now()
            start_date = end_date - timedelta(days=30)
            
            daily_reports = DisciplineReport.objects.filter(
                reported_at__date__range=(start_date.date(), end_date.date())
            ).values('reported_at__date').annotate(count=Count('id')).order_by('reported_at__date')
            
            return {
                'success': True,
                'trends': list(daily_reports),
                'period_days': 30,
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_category_distribution(self):
        try:
            categories = DisciplineReport.objects.values(
                'category__name', 'category__key'
            ).annotate(
                count=Count('id'),
                avg_points=Avg('points'),
            ).order_by('-count')[:15]
            
            total = DisciplineReport.objects.count()
            distribution = []
            for cat in categories:
                distribution.append({
                    'name': cat['category__name'],
                    'key': cat['category__key'],
                    'count': cat['count'],
                    'percentage': round((cat['count'] / total * 100), 1) if total else 0,
                    'avg_points': round(cat['avg_points'], 1) if cat['avg_points'] else 0,
                })
            
            return {'success': True, 'distribution': distribution}
        except Exception as e:
            return {'success': False, 'error': str(e)}


# ============================================
# AETHER CHAT CONTEXT BUILDER
# ============================================

class AetherContextBuilder:
    """Build rich context for Aether AI conversations"""
    
    @staticmethod
    def build_school_context():
        school = School.objects.first()
        if not school:
            return {}
        
        total_students = Student.objects.filter(is_active=True).count()
        total_reports = DisciplineReport.objects.count()
        total_teachers = TeacherProfile.objects.filter(is_approved=True).count()
        
        return {
            'school_name': school.name,
            'total_students': total_students,
            'total_reports': total_reports,
            'total_teachers': total_teachers,
            'critical_students': Student.objects.filter(is_active=True, risk_level='CRITICAL').count(),
            'warning_students': Student.objects.filter(is_active=True, risk_level='WARNING').count(),
            'good_students': Student.objects.filter(is_active=True, risk_level='GOOD').count(),
        }
    
    @staticmethod
    def build_student_context(student_id):
        try:
            student = Student.objects.get(id=student_id, is_active=True)
            reports = student.reports.select_related('category').order_by('-reported_at')[:10]
            
            category_breakdown = reports.values('category__name').annotate(count=Count('id')).order_by('-count')
            
            return {
                'student_id': student.id,
                'name': student.name,
                'admission_number': student.admission_number,
                'stream': student.stream.name if student.stream else 'N/A',
                'form': student.form,
                'risk_score': student.risk_score,
                'risk_level': student.risk_level,
                'total_reports': student.total_reports,
                'intervention_count': student.intervention_count,
                'days_since_incident': student.days_since_last_incident,
                'risk_trend': student.risk_trend,
                'category_breakdown': list(category_breakdown),
                'recent_reports': [
                    {
                        'date': r.reported_at.strftime('%Y-%m-%d'),
                        'category': r.category.name,
                        'rating': r.rating,
                        'points': r.points,
                    } for r in reports[:5]
                ],
            }
        except Student.DoesNotExist:
            return {}
    
    @staticmethod
    def build_stream_context(stream_id):
        try:
            stream = Stream.objects.get(id=stream_id, is_active=True)
            students = Student.objects.filter(stream=stream, is_active=True)
            total = students.count()
            
            return {
                'stream_name': stream.name,
                'total_students': total,
                'critical_count': students.filter(risk_level='CRITICAL').count(),
                'warning_count': students.filter(risk_level='WARNING').count(),
                'good_count': students.filter(risk_level='GOOD').count(),
                'avg_risk': round(students.aggregate(avg=Avg('risk_score'))['avg'] or 0, 1),
            }
        except Stream.DoesNotExist:
            return {}


# ============================================
# AI RECOMMENDATION ENGINE
# ============================================

class AIRecommendationEngine:
    """Generates AI-powered recommendations for students and school"""
    
    def __init__(self):
        self.ai = AetherAI()
    
    def get_student_recommendations(self, student_id):
        try:
            student = Student.objects.get(id=student_id, is_active=True)
            result = self.ai.analyze_student_behavior(student_id)
            
            if result['success']:
                analysis = result.get('analysis', {})
                recommendations = []
                
                if student.risk_level == 'CRITICAL':
                    recommendations.append({
                        'priority': 'URGENT',
                        'action': 'Immediate Crisis Intervention',
                        'description': analysis.get('assessment', 'Student requires immediate attention'),
                        'steps': [
                            'Schedule emergency parent-teacher conference',
                            'Refer to school counselor',
                            'Create behavior intervention plan',
                            'Document all interventions',
                            'Schedule weekly review meetings',
                        ]
                    })
                elif student.risk_level == 'WARNING':
                    recommendations.append({
                        'priority': 'HIGH',
                        'action': 'Preventive Intervention',
                        'description': 'Student needs support before escalation',
                        'steps': [
                            'Schedule parent meeting within 1 week',
                            'Assign mentor or peer buddy',
                            'Create behavior tracking sheet',
                            'Monitor daily for 2 weeks',
                        ]
                    })
                else:
                    recommendations.append({
                        'priority': 'LOW',
                        'action': 'Maintain Positive Behavior',
                        'description': 'Student is doing well. Continue positive reinforcement.',
                        'steps': [
                            'Continue positive reinforcement',
                            'Maintain regular monitoring',
                            'Encourage leadership opportunities',
                        ]
                    })
                
                return {
                    'success': True,
                    'student_name': student.name,
                    'risk_level': student.risk_level,
                    'risk_score': student.risk_score,
                    'analysis': analysis,
                    'recommendations': recommendations,
                }
            return result
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_school_recommendations(self):
        try:
            students = Student.objects.filter(is_active=True)
            total = students.count()
            critical = students.filter(risk_level='CRITICAL').count()
            warning = students.filter(risk_level='WARNING').count()
            
            recommendations = []
            
            if critical > 0:
                recommendations.append({
                    'priority': 'HIGH',
                    'action': f'Immediate Intervention for {critical} Critical Students',
                    'description': f'{critical} students require immediate attention out of {total} total.',
                    'steps': [
                        'Schedule parent-teacher meetings for all critical students',
                        'Assign counselors for assessment',
                        'Create behavior modification plans',
                        'Track intervention progress weekly',
                    ]
                })
            
            if warning > total * 0.3:
                recommendations.append({
                    'priority': 'MEDIUM',
                    'action': f'Address Rising Warning Levels ({warning} students)',
                    'description': f'{warning} students at warning level - potential escalation risk.',
                    'steps': [
                        'Increase monitoring and teacher attention',
                        'Contact parents for awareness',
                        'Document all incidents',
                        'Create individual improvement plans',
                    ]
                })
            
            top_categories = DisciplineReport.objects.values('category__name').annotate(count=Count('id')).order_by('-count')[:3]
            if top_categories:
                top_cat = top_categories[0]
                recommendations.append({
                    'priority': 'MEDIUM',
                    'action': f'Address Dominant Category: {top_cat["category__name"]}',
                    'description': f'{top_cat["count"]} reports in this category. Consider targeted interventions.',
                    'steps': [
                        'Review category-specific policies',
                        'Create awareness campaigns',
                        'Train teachers on prevention strategies',
                        'Monitor effectiveness over 4 weeks',
                    ]
                })
            
            return {'success': True, 'recommendations': recommendations}
        except Exception as e:
            return {'success': False, 'error': str(e)}
