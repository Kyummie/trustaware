import json
import os
import re
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from app.decorators import role_required, login_required
from app.supabase_client import supabase_admin

try:
    import anthropic
except ImportError:
    anthropic = None


TOPIC_COLORS = {
    'Email Security': 'topic-blue',
    'Access Control': 'topic-purple',
    'Human Factor': 'topic-orange',
    'Hygiene': 'topic-teal',
    'Compliance': 'topic-cyan',
    'Response': 'topic-red',
    'Malware': 'topic-pink',
}

OPTION_KEYS = ('a', 'b', 'c', 'd')
OPTION_LETTERS = ('A', 'B', 'C', 'D')


def _initials(full_name):
    if not full_name:
        return '?'
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _format_date(iso_string):
    if not iso_string:
        return None
    try:
        return datetime.fromisoformat(iso_string.replace('Z', '+00:00')).strftime('%-d %b %Y')
    except ValueError:
        return None


def _topic_class(topic):
    return TOPIC_COLORS.get(topic, 'topic-grey')


def _parse_duration(raw):
    """Accepts '10', '10 min', '10 minutes', etc. Returns an int or None."""
    if not raw:
        return None
    match = re.match(r'\s*(\d+)', raw)
    return int(match.group(1)) if match else None


def _video_view(v):
    duration = v.get('duration_minutes')
    return {
        'id': v['id'],
        'title': v.get('title') or None,
        'video_url': v.get('video_url'),
        'duration_minutes': duration,
        'duration_display': f'{duration} min' if duration else None,
        'position': v.get('position') or 0,
    }


def _module_view(m, assigned_count=None):
    """m may come from a `select('*, module_videos(*)')` query, in which
    case m['module_videos'] is the nested list of that module's videos. A
    module can hold any number of videos now (previously exactly one), so
    duration and video count are always derived from that list rather
    than stored on the module itself."""
    videos = sorted(
        [_video_view(v) for v in (m.get('module_videos') or [])],
        key=lambda v: v['position'],
    )
    total_minutes = sum(v['duration_minutes'] for v in videos if v['duration_minutes'])

    view = {k: v for k, v in m.items() if k != 'module_videos'}
    view['topic_class'] = _topic_class(m.get('topic'))
    view['videos'] = videos
    view['video_count'] = len(videos)
    view['duration_display'] = f'{total_minutes} min' if total_minutes else None
    if assigned_count is not None:
        view['assigned_count'] = assigned_count
    return view


def _generate_quiz_question(topic, title):
    """Calls the Claude API to generate one multiple-choice quiz question.
    Returns {question_text, options, correct_answer} or raises. This is
    deliberately best-effort: every route that calls this catches the
    exception and falls back to telling the operator to edit the
    question manually, since manual add/edit/delete is the primary path
    and AI regeneration is an optional convenience on top of it."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key or anthropic is None:
        raise RuntimeError(
            'ANTHROPIC_API_KEY is not set, so AI question generation is unavailable. '
            'Add or edit this question manually instead.'
        )
    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        f'Write one multiple-choice cybersecurity awareness quiz question for a workplace '
        f'training module titled "{title}" on the topic of "{topic}". '
        f'Respond with ONLY valid JSON in this exact shape, no other text: '
        f'{{"question_text": "...", "options": ["...", "...", "...", "..."], "correct_answer": "..."}}. '
        f'correct_answer must be an exact copy of one of the four options.'
    )
    message = client.messages.create(
        model='claude-sonnet-4-5',
        max_tokens=500,
        messages=[{'role': 'user', 'content': prompt}],
    )
    raw = message.content[0].text.strip()
    data = json.loads(raw)
    if data.get('correct_answer') not in (data.get('options') or []):
        raise ValueError('The model did not return a correct_answer matching one of its own options.')
    return data


cisoaas_bp = Blueprint('cisoaas', __name__, url_prefix='/cisoaas')


def _require_admin_client():
    """All CISOaaS operations use the service-role client. This route
    is already gated to role='cisoaas' at the app layer (role_required),
    so bypassing RLS here is intentional, not a hole."""
    if supabase_admin is None:
        flash('Server is missing SUPABASE_SECRET_KEY in .env.', 'error')
        return False
    return True


@cisoaas_bp.route('/')
@role_required('cisoaas')
def dashboard():
    if not _require_admin_client():
        return render_template(
            'cisoaas/overview.html',
            organisations=[],
            stats={'sme_clients': 0, 'total_employees': 0, 'avg_completion': None, 'module_count': 0},
            active_nav='overview',
        )

    orgs_result = supabase_admin.table('organisations').select('*').order('created_at', desc=True).execute()
    organisations = orgs_result.data or []

    profiles_result = supabase_admin.table('profiles').select('id, organisation_id, full_name, role').execute()
    profiles = profiles_result.data or []

    modules_result = supabase_admin.table('training_modules').select('id').execute()
    total_modules = len(modules_result.data or [])

    assignments_result = supabase_admin.table('module_assignments').select('organisation_id').execute()
    assignments = assignments_result.data or []
    modules_by_org = {}
    for a in assignments:
        oid = a.get('organisation_id')
        modules_by_org[oid] = modules_by_org.get(oid, 0) + 1

    org_rows = []
    total_employees = 0
    for org in organisations:
        org_profiles = [p for p in profiles if p.get('organisation_id') == org['id']]
        employees = [p for p in org_profiles if p.get('role') == 'employee']
        admin = next((p for p in org_profiles if p.get('role') == 'sme_admin'), None)
        total_employees += len(employees)
        org_rows.append({
            'id': org['id'],
            'name': org['name'],
            'domain': org['domain'],
            'admin_name': admin['full_name'] if admin else None,
            'employee_count': len(employees),
            # Completion tracking isn't built yet, kept as an honest
            # "not tracked yet" state rather than a fake number.
            'completion_pct': None,
            'module_count': modules_by_org.get(org['id'], 0),
        })

    stats = {
        'sme_clients': len(organisations),
        'total_employees': total_employees,
        'avg_completion': None,
        'module_count': total_modules,
    }

    return render_template(
        'cisoaas/overview.html',
        organisations=org_rows,
        stats=stats,
        active_nav='overview',
    )


@cisoaas_bp.route('/onboard', methods=['POST'])
@role_required('cisoaas')
def onboard():
    if not _require_admin_client():
        return redirect(url_for('cisoaas.dashboard'))

    company_name = request.form.get('company_name', '').strip()
    domain = request.form.get('domain', '').strip().lower()
    admin_name = request.form.get('admin_name', '').strip()
    admin_email = request.form.get('admin_email', '').strip().lower()
    temp_password = request.form.get('temp_password', '')

    if not all([company_name, domain, admin_name, admin_email, temp_password]):
        flash('Please fill in every field before creating the organisation.', 'error')
        return redirect(url_for('cisoaas.dashboard'))

    # 1. Create the organisation
    try:
        org_result = supabase_admin.table('organisations').insert({
            'name': company_name,
            'domain': domain,
        }).execute()
        org = org_result.data[0]
    except Exception as e:
        flash(f'Could not create the organisation: {e}', 'error')
        return redirect(url_for('cisoaas.dashboard'))

    # 2. Create the SME admin's auth account. Passing user_metadata here
    # means the handle_new_user() trigger creates the profiles row with
    # the correct full_name/role immediately, no follow-up patch needed
    # for those two fields.
    try:
        admin_user = supabase_admin.auth.admin.create_user({
            'email': admin_email,
            'password': temp_password,
            'email_confirm': True,
            'user_metadata': {
                'full_name': admin_name,
                'role': 'sme_admin',
            },
        })
    except Exception as e:
        # Roll back the organisation so we don't leave an orphaned row
        # with no admin behind it.
        supabase_admin.table('organisations').delete().eq('id', org['id']).execute()
        flash(f'Could not create the admin account: {e}', 'error')
        return redirect(url_for('cisoaas.dashboard'))

    # 3. Attach the new profile to its organisation. The trigger has no
    # way to know organisation_id, so this is a required follow-up step.
    supabase_admin.table('profiles').update({
        'organisation_id': org['id'],
    }).eq('id', admin_user.user.id).execute()

    flash(f'{company_name} onboarded. Admin account created for {admin_email}.', 'success')
    return redirect(url_for('cisoaas.dashboard'))


@cisoaas_bp.route('/organisations/<org_id>')
@role_required('cisoaas')
def org_detail(org_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.dashboard'))

    org_result = supabase_admin.table('organisations').select('*').eq('id', org_id).single().execute()
    org = org_result.data
    if not org:
        flash('Organisation not found.', 'error')
        return redirect(url_for('cisoaas.dashboard'))

    profiles_result = supabase_admin.table('profiles').select('*').eq('organisation_id', org_id).execute()
    profiles = profiles_result.data or []

    # profiles doesn't store email, pull it from auth.users via the admin API
    users = supabase_admin.auth.admin.list_users()
    email_by_id = {u.id: u.email for u in users}

    employees = []
    admin_profile = None
    for p in profiles:
        p_view = {
            'id': p['id'],
            'full_name': p.get('full_name'),
            'initials': _initials(p.get('full_name')),
            'email': email_by_id.get(p['id'], 'unknown'),
            'created_at': p.get('created_at'),
        }
        if p.get('role') == 'employee':
            employees.append(p_view)
        elif p.get('role') == 'sme_admin':
            admin_profile = p_view

    org['onboarded_display'] = _format_date(org.get('created_at'))

    # Module library: everything assigned to this org, and everything
    # still available to assign (the whole library minus what's assigned).
    # `module_videos(*)` embeds each module's videos in one query so
    # duration/video-count can be computed without an extra round trip.
    all_modules_result = (
        supabase_admin.table('training_modules')
        .select('*, module_videos(*)')
        .order('created_at', desc=True)
        .execute()
    )
    all_modules = [_module_view(m) for m in (all_modules_result.data or [])]

    assignments_result = (
        supabase_admin.table('module_assignments')
        .select('module_id')
        .eq('organisation_id', org_id)
        .execute()
    )
    assigned_ids = {a['module_id'] for a in (assignments_result.data or [])}

    assigned_modules = [m for m in all_modules if m['id'] in assigned_ids]
    available_modules = [m for m in all_modules if m['id'] not in assigned_ids]

    return render_template(
        'cisoaas/org_detail.html',
        org=org,
        admin=admin_profile,
        employees=employees,
        assigned_modules=assigned_modules,
        available_modules=available_modules,
        module_count=len(assigned_modules),
        active_nav='overview',
    )


@cisoaas_bp.route('/organisations/<org_id>/employees', methods=['POST'])
@role_required('cisoaas')
def add_employee(org_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.org_detail', org_id=org_id))

    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    temp_password = request.form.get('temp_password', '')

    if not all([full_name, email, temp_password]):
        flash('Please fill in every field before adding the employee.', 'error')
        return redirect(url_for('cisoaas.org_detail', org_id=org_id))

    try:
        new_user = supabase_admin.auth.admin.create_user({
            'email': email,
            'password': temp_password,
            'email_confirm': True,
            'user_metadata': {
                'full_name': full_name,
                'role': 'employee',
            },
        })
    except Exception as e:
        flash(f'Could not create the employee account: {e}', 'error')
        return redirect(url_for('cisoaas.org_detail', org_id=org_id))

    supabase_admin.table('profiles').update({
        'organisation_id': org_id,
    }).eq('id', new_user.user.id).execute()

    flash(f'{full_name} added to the organisation.', 'success')
    return redirect(url_for('cisoaas.org_detail', org_id=org_id))


@cisoaas_bp.route('/organisations/<org_id>/modules/assign', methods=['POST'])
@role_required('cisoaas')
def assign_module(org_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.org_detail', org_id=org_id))

    module_id = request.form.get('module_id', '')
    if not module_id:
        flash('Please choose a module to assign.', 'error')
        return redirect(url_for('cisoaas.org_detail', org_id=org_id))

    try:
        supabase_admin.table('module_assignments').insert({
            'module_id': module_id,
            'organisation_id': org_id,
        }).execute()
        flash('Module assigned to this organisation.', 'success')
    except Exception as e:
        flash(f'Could not assign the module: {e}', 'error')

    return redirect(url_for('cisoaas.org_detail', org_id=org_id))


@cisoaas_bp.route('/organisations/<org_id>/modules/<module_id>/unassign', methods=['POST'])
@role_required('cisoaas')
def unassign_module(org_id, module_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.org_detail', org_id=org_id))

    supabase_admin.table('module_assignments').delete().eq('module_id', module_id).eq('organisation_id', org_id).execute()
    flash('Module removed from this organisation.', 'success')
    return redirect(url_for('cisoaas.org_detail', org_id=org_id))


@cisoaas_bp.route('/modules')
@role_required('cisoaas')
def modules():
    if not _require_admin_client():
        return render_template('cisoaas/modules.html', modules=[], topic_choices=TOPIC_COLORS.keys(), active_nav='modules')

    modules_result = (
        supabase_admin.table('training_modules')
        .select('*, module_videos(*)')
        .order('created_at', desc=True)
        .execute()
    )
    all_modules = modules_result.data or []

    assignments_result = supabase_admin.table('module_assignments').select('module_id').execute()
    assignments = assignments_result.data or []
    assigned_count_by_module = {}
    for a in assignments:
        mid = a.get('module_id')
        assigned_count_by_module[mid] = assigned_count_by_module.get(mid, 0) + 1

    module_rows = [_module_view(m, assigned_count_by_module.get(m['id'], 0)) for m in all_modules]

    return render_template(
        'cisoaas/modules.html',
        modules=module_rows,
        topic_choices=TOPIC_COLORS.keys(),
        active_nav='modules',
    )


@cisoaas_bp.route('/modules/create', methods=['POST'])
@role_required('cisoaas')
def create_module():
    if not _require_admin_client():
        return redirect(url_for('cisoaas.modules'))

    title = request.form.get('title', '').strip()
    topic = request.form.get('topic', '').strip()

    if not title or not topic:
        flash('Please fill in at least the module title and topic.', 'error')
        return redirect(url_for('cisoaas.modules'))

    # A module can now hold several videos (e.g. more than one phishing
    # clip), so the form sends parallel lists rather than a single URL.
    # Rows where the URL was left blank are skipped; title and duration
    # are optional per video.
    video_titles = request.form.getlist('video_title')
    video_urls = request.form.getlist('video_url')
    video_durations = request.form.getlist('video_duration')

    try:
        module_result = supabase_admin.table('training_modules').insert({
            'title': title,
            'topic': topic,
        }).execute()
        module = module_result.data[0]
    except Exception as e:
        flash(f'Could not create the module: {e}', 'error')
        return redirect(url_for('cisoaas.modules'))

    videos_to_insert = []
    position = 0
    for i, url in enumerate(video_urls):
        url = url.strip()
        if not url:
            continue
        video_title = video_titles[i].strip() if i < len(video_titles) else ''
        duration_raw = video_durations[i] if i < len(video_durations) else ''
        videos_to_insert.append({
            'module_id': module['id'],
            'title': video_title or None,
            'video_url': url,
            'duration_minutes': _parse_duration(duration_raw),
            'position': position,
        })
        position += 1

    if videos_to_insert:
        try:
            supabase_admin.table('module_videos').insert(videos_to_insert).execute()
        except Exception as e:
            flash(f'Module created, but the videos could not be saved: {e}. Add them from the module page instead.', 'error')
            return redirect(url_for('cisoaas.module_detail', module_id=module['id']))

    flash(f'"{title}" added to the module library.', 'success')
    return redirect(url_for('cisoaas.modules'))


@cisoaas_bp.route('/modules/<module_id>')
@role_required('cisoaas')
def module_detail(module_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.modules'))

    module_result = (
        supabase_admin.table('training_modules')
        .select('*, module_videos(*)')
        .eq('id', module_id)
        .single()
        .execute()
    )
    module = module_result.data
    if not module:
        flash('Module not found.', 'error')
        return redirect(url_for('cisoaas.modules'))

    questions_result = (
        supabase_admin.table('quiz_questions')
        .select('*')
        .eq('module_id', module_id)
        .order('created_at')
        .execute()
    )
    questions = questions_result.data or []

    question_rows = []
    for q in questions:
        opts = q.get('options') or []
        question_rows.append({
            'id': q['id'],
            'question_text': q.get('question_text'),
            'options': opts,
            'correct_answer': q.get('correct_answer'),
            'labeled_options': list(zip(OPTION_LETTERS, opts)),
        })

    return render_template(
        'cisoaas/module_detail.html',
        module=_module_view(module),
        questions=question_rows,
        active_nav='modules',
    )


@cisoaas_bp.route('/modules/<module_id>/videos', methods=['POST'])
@role_required('cisoaas')
def add_video(module_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    video_url = request.form.get('video_url', '').strip()
    video_title = request.form.get('video_title', '').strip()
    duration_minutes = _parse_duration(request.form.get('video_duration', ''))

    if not video_url:
        flash('Please provide a video URL.', 'error')
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    existing = (
        supabase_admin.table('module_videos')
        .select('position')
        .eq('module_id', module_id)
        .order('position', desc=True)
        .limit(1)
        .execute()
    )
    next_position = (existing.data[0]['position'] + 1) if existing.data else 0

    try:
        supabase_admin.table('module_videos').insert({
            'module_id': module_id,
            'title': video_title or None,
            'video_url': video_url,
            'duration_minutes': duration_minutes,
            'position': next_position,
        }).execute()
        flash('Video added.', 'success')
    except Exception as e:
        flash(f'Could not add the video: {e}', 'error')

    return redirect(url_for('cisoaas.module_detail', module_id=module_id))


@cisoaas_bp.route('/modules/<module_id>/videos/<video_id>/edit', methods=['POST'])
@role_required('cisoaas')
def edit_video(module_id, video_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    video_url = request.form.get('video_url', '').strip()
    video_title = request.form.get('video_title', '').strip()
    duration_minutes = _parse_duration(request.form.get('video_duration', ''))

    if not video_url:
        flash('Please provide a video URL.', 'error')
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    try:
        supabase_admin.table('module_videos').update({
            'title': video_title or None,
            'video_url': video_url,
            'duration_minutes': duration_minutes,
        }).eq('id', video_id).execute()
        flash('Video updated.', 'success')
    except Exception as e:
        flash(f'Could not update the video: {e}', 'error')

    return redirect(url_for('cisoaas.module_detail', module_id=module_id))


@cisoaas_bp.route('/modules/<module_id>/videos/<video_id>/delete', methods=['POST'])
@role_required('cisoaas')
def delete_video(module_id, video_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    supabase_admin.table('module_videos').delete().eq('id', video_id).execute()
    flash('Video removed.', 'success')
    return redirect(url_for('cisoaas.module_detail', module_id=module_id))


def _read_question_form():
    question_text = request.form.get('question_text', '').strip()
    options = [request.form.get(f'option_{k}', '').strip() for k in OPTION_KEYS]
    correct_key = request.form.get('correct_option', '')
    if not question_text or not all(options) or correct_key not in OPTION_KEYS:
        return None, None, None
    correct_answer = options[OPTION_KEYS.index(correct_key)]
    return question_text, options, correct_answer


@cisoaas_bp.route('/modules/<module_id>/questions', methods=['POST'])
@role_required('cisoaas')
def create_question(module_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    question_text, options, correct_answer = _read_question_form()
    if question_text is None:
        flash('Please fill in the question, all four options, and mark which one is correct.', 'error')
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    try:
        supabase_admin.table('quiz_questions').insert({
            'module_id': module_id,
            'question_text': question_text,
            'options': options,
            'correct_answer': correct_answer,
        }).execute()
        flash('Question added.', 'success')
    except Exception as e:
        flash(f'Could not add the question: {e}', 'error')

    return redirect(url_for('cisoaas.module_detail', module_id=module_id))


@cisoaas_bp.route('/modules/<module_id>/questions/<question_id>/edit', methods=['POST'])
@role_required('cisoaas')
def edit_question(module_id, question_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    question_text, options, correct_answer = _read_question_form()
    if question_text is None:
        flash('Please fill in the question, all four options, and mark which one is correct.', 'error')
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    try:
        supabase_admin.table('quiz_questions').update({
            'question_text': question_text,
            'options': options,
            'correct_answer': correct_answer,
        }).eq('id', question_id).execute()
        flash('Question updated.', 'success')
    except Exception as e:
        flash(f'Could not update the question: {e}', 'error')

    return redirect(url_for('cisoaas.module_detail', module_id=module_id))


@cisoaas_bp.route('/modules/<module_id>/questions/<question_id>/delete', methods=['POST'])
@role_required('cisoaas')
def delete_question(module_id, question_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    supabase_admin.table('quiz_questions').delete().eq('id', question_id).execute()
    flash('Question deleted.', 'success')
    return redirect(url_for('cisoaas.module_detail', module_id=module_id))


@cisoaas_bp.route('/modules/<module_id>/questions/<question_id>/regenerate', methods=['POST'])
@role_required('cisoaas')
def regenerate_question(module_id, question_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    module_result = supabase_admin.table('training_modules').select('*').eq('id', module_id).single().execute()
    module = module_result.data
    if not module:
        flash('Module not found.', 'error')
        return redirect(url_for('cisoaas.modules'))

    try:
        generated = _generate_quiz_question(module.get('topic', ''), module.get('title', ''))
    except Exception as e:
        flash(f'Could not regenerate this question with AI ({e}). You can still edit it manually below.', 'error')
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    supabase_admin.table('quiz_questions').update({
        'question_text': generated['question_text'],
        'options': generated['options'],
        'correct_answer': generated['correct_answer'],
    }).eq('id', question_id).execute()
    flash('Question regenerated with AI. Review it below and edit if needed.', 'success')
    return redirect(url_for('cisoaas.module_detail', module_id=module_id))


@cisoaas_bp.route('/modules/<module_id>/questions/regenerate-all', methods=['POST'])
@role_required('cisoaas')
def regenerate_all_questions(module_id):
    if not _require_admin_client():
        return redirect(url_for('cisoaas.module_detail', module_id=module_id))

    module_result = supabase_admin.table('training_modules').select('*').eq('id', module_id).single().execute()
    module = module_result.data
    if not module:
        flash('Module not found.', 'error')
        return redirect(url_for('cisoaas.modules'))

    questions_result = supabase_admin.table('quiz_questions').select('id').eq('module_id', module_id).execute()
    questions = questions_result.data or []

    failures = 0
    for q in questions:
        try:
            generated = _generate_quiz_question(module.get('topic', ''), module.get('title', ''))
            supabase_admin.table('quiz_questions').update({
                'question_text': generated['question_text'],
                'options': generated['options'],
                'correct_answer': generated['correct_answer'],
            }).eq('id', q['id']).execute()
        except Exception:
            failures += 1

    if not questions:
        flash('This module has no questions yet. Add one first, then regenerate it.', 'error')
    elif failures:
        flash(
            f'Regenerated {len(questions) - failures} of {len(questions)} questions with AI. '
            f'{failures} could not be regenerated, review and edit them manually below.',
            'error',
        )
    else:
        flash(f'Regenerated all {len(questions)} questions with AI. Review them below and edit if needed.', 'success')

    return redirect(url_for('cisoaas.module_detail', module_id=module_id))

@cisoaas_bp.route('/modules/<module_id>/delete', methods=['POST'])
@login_required
@role_required('cisoaas')
def delete_module(module_id):
    back = request.referrer or '/cisoaas'

    found = supabase_admin.table('training_modules').select('id, title').eq('id', module_id).limit(1).execute()
    if not found.data:
        abort(404)
    title = found.data[0]['title']

    done = supabase_admin.table('completion_records').select('id').eq('module_id', module_id).limit(1).execute()
    if done.data:
        flash(f'"{title}" cannot be deleted because employees have completed it. Completion records are audit evidence and are kept.', 'error')
        return redirect(back)

    try:
        # Remove everything that points to this module, then the module itself
        for table in ('module_assignments', 'quiz_attempts', 'quiz_questions', 'module_videos'):
            supabase_admin.table(table).delete().eq('module_id', module_id).execute()
        supabase_admin.table('training_modules').delete().eq('id', module_id).execute()
    except Exception:
        flash(f'Could not delete "{title}". Nothing else was changed.', 'error')
        return redirect(back)

    flash(f'"{title}" has been deleted.', 'success')
    return redirect(back)