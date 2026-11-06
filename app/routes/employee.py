import random
import re
from flask import Blueprint, render_template, session, request, redirect, url_for, abort
from app.decorators import login_required, role_required
from app.supabase_client import supabase_admin

employee_bp = Blueprint('employee', __name__)

PASS_MARK = 70
QUIZ_SIZE = 10


def _embed_url(url):
    match = re.search(r'(?:v=|youtu\.be/|embed/)([A-Za-z0-9_-]{11})', url or '')
    return f'https://www.youtube.com/embed/{match.group(1)}' if match else None


def _assigned_module(module_id):
    """Return the module only if it is assigned to the employee's organisation."""
    org_id = session.get('organisation_id')
    if not org_id:
        return None
    link = (supabase_admin.table('module_assignments').select('id')
            .eq('organisation_id', org_id).eq('module_id', module_id).limit(1).execute())
    if not link.data:
        return None
    module = (supabase_admin.table('training_modules').select('*, module_videos(*)')
              .eq('id', module_id).limit(1).execute())
    return module.data[0] if module.data else None


@employee_bp.route('/dashboard')
@login_required
@role_required('employee')
def dashboard():
    user_id = session['user_id']
    org_id = session.get('organisation_id')

    profile = supabase_admin.table('profiles').select('full_name').eq('id', user_id).limit(1).execute()
    full_name = profile.data[0]['full_name'] if profile.data else session.get('email')

    modules = []
    if org_id:
        assigned = supabase_admin.table('module_assignments').select('module_id').eq('organisation_id', org_id).execute()
        module_ids = [row['module_id'] for row in assigned.data]

        if module_ids:
            found = supabase_admin.table('training_modules').select('*, module_videos(*)').in_('id', module_ids).execute()
            done = supabase_admin.table('completion_records').select('module_id').eq('employee_id', user_id).in_('module_id', module_ids).execute()
            done_ids = {row['module_id'] for row in done.data}

            for m in found.data:
                videos = m.get('module_videos') or []
                modules.append({
                    'id': m['id'],
                    'title': m['title'],
                    'topic': m['topic'],
                    'video_count': len(videos),
                    'minutes': sum(v.get('duration_minutes') or 0 for v in videos),
                    'completed': m['id'] in done_ids,
                })

    completed = sum(1 for m in modules if m['completed'])
    return render_template('employee/dashboard.html', full_name=full_name,
                           modules=modules, total=len(modules), completed=completed)


@employee_bp.route('/module/<module_id>')
@login_required
@role_required('employee')
def module_page(module_id):
    module = _assigned_module(module_id)
    if not module:
        abort(404)

    videos = sorted(module.get('module_videos') or [], key=lambda v: v['position'])
    for v in videos:
        v['embed_url'] = _embed_url(v['video_url'])

    done = (supabase_admin.table('completion_records').select('score_at_completion, completed_at')
            .eq('employee_id', session['user_id']).eq('module_id', module_id).limit(1).execute())
    completion = done.data[0] if done.data else None

    return render_template('employee/module.html', module=module, videos=videos, completion=completion)


@employee_bp.route('/module/<module_id>/quiz')
@login_required
@role_required('employee')
def quiz(module_id):
    module = _assigned_module(module_id)
    if not module:
        abort(404)

    bank = supabase_admin.table('quiz_questions').select('*').eq('module_id', module_id).execute().data
    if not bank:
        return render_template('employee/quiz.html', module=module, questions=[])

    chosen = random.sample(bank, min(QUIZ_SIZE, len(bank)))
    session[f'quiz_{module_id}'] = [q['id'] for q in chosen]

    questions = []
    for q in chosen:
        options = [(key, q[f'option_{key}']) for key in 'abcd']
        random.shuffle(options)
        questions.append({'id': q['id'], 'text': q['question_text'], 'options': options})

    return render_template('employee/quiz.html', module=module, questions=questions)


@employee_bp.route('/module/<module_id>/quiz/submit', methods=['POST'])
@login_required
@role_required('employee')
def submit_quiz(module_id):
    module = _assigned_module(module_id)
    if not module:
        abort(404)

    question_ids = session.pop(f'quiz_{module_id}', None)
    if not question_ids:
        return redirect(url_for('employee.quiz', module_id=module_id))

    # Grade on the server using the stored correct answers, never the browser's
    rows = supabase_admin.table('quiz_questions').select('id, correct_option').in_('id', question_ids).execute().data
    answer_key = {r['id']: r['correct_option'] for r in rows}

    total = len(question_ids)
    correct = sum(1 for qid in question_ids if request.form.get(f'q_{qid}') == answer_key.get(qid))
    score = round(correct / total * 100)
    passed = score >= PASS_MARK
    user_id = session['user_id']

    supabase_admin.table('quiz_attempts').insert({
        'employee_id': user_id,
        'module_id': module_id,
        'score': score,
        'passed': passed,
    }).execute()

    if passed:
        existing = (supabase_admin.table('completion_records').select('id')
                    .eq('employee_id', user_id).eq('module_id', module_id).limit(1).execute())
        if not existing.data:
            supabase_admin.table('completion_records').insert({
                'employee_id': user_id,
                'module_id': module_id,
                'organisation_id': session['organisation_id'],
                'score_at_completion': score,
            }).execute()

    return render_template('employee/result.html', module=module, score=score, correct=correct,
                           total=total, passed=passed, pass_mark=PASS_MARK)