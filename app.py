# app.py
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import json
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change_this_secret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hospital.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


# -------------------------
# Models
# -------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'admin', 'doctor', 'patient'
    full_name = db.Column(db.String(120))
    contact = db.Column(db.String(50))
    active = db.Column(db.Boolean, default=True)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    description = db.Column(db.Text)
    doctors = db.relationship('Doctor', backref='department', lazy=True)


class Doctor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    specialization = db.Column(db.String(120))
    availability_json = db.Column(db.Text)  # {"YYYY-MM-DD": ["09:00","10:00"]}
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'), nullable=True)

    user = db.relationship('User')


class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    age = db.Column(db.Integer)
    medical_info = db.Column(db.Text)

    user = db.relationship('User')


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctor.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(20), nullable=False)  # ex: '09:00'
    status = db.Column(db.String(20), default='Booked')  # Booked / Completed / Cancelled
    # treatments relationship below

    patient = db.relationship('Patient')
    doctor = db.relationship('Doctor')

    __table_args__ = (
        db.UniqueConstraint('doctor_id', 'date', 'time', name='uix_doctor_datetime'),
    )


class Treatment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointment.id'), nullable=False)
    diagnosis = db.Column(db.Text)
    prescription = db.Column(db.Text)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    appointment = db.relationship('Appointment', backref=db.backref('treatments', cascade='all, delete-orphan'))


# -------------------------
# Login manager
# -------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# -------------------------
# Helpers
# -------------------------
def create_default_data():
    """Create admin user and some departments programmatically if missing."""
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(username='admin', role='admin', full_name='Administrator', active=True)
        admin.set_password('admin123')
        db.session.add(admin)
    # sample departments
    for deptname in ['Cardiology', 'Oncology', 'General', 'Orthopedics', 'ENT']:
        if not Department.query.filter_by(name=deptname).first():
            db.session.add(Department(name=deptname, description=f'{deptname} department'))
    db.session.commit()


def next_n_dates(n=7):
    base = date.today()
    return [(base + timedelta(days=i)) for i in range(n)]


# -------------------------
# Routes - Auth
# -------------------------


@app.route('/')
def home():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        if current_user.role == 'doctor':
            return redirect(url_for('doctor_dashboard'))
        if current_user.role == 'patient':
            return redirect(url_for('patient_dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.active:
            login_user(user)
            flash('Logged in', 'success')
            return redirect(url_for('home'))
        flash('Invalid credentials or account inactive', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        full_name = request.form.get('full_name', '').strip()
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('register'))
        user = User(username=username, role='patient', full_name=full_name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        patient = Patient(user_id=user.id)
        db.session.add(patient)
        db.session.commit()
        flash('Registered. Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


# -------------------------
# Admin routes
# -------------------------
@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin':
        flash('Unauthorized', 'danger')
        return redirect(url_for('home'))
    total_doctors = Doctor.query.count()
    total_patients = Patient.query.count()
    total_appointments = Appointment.query.count()
    doctors = Doctor.query.all()
    patients = Patient.query.all()
    appointments = Appointment.query.order_by(Appointment.date.desc()).all()
    return render_template('admin_dashboard.html', doctors=doctors, patients=patients,
                           appointments=appointments, total_doctors=total_doctors,
                           total_patients=total_patients, total_appointments=total_appointments)


@app.route('/admin/create_doctor', methods=['GET', 'POST'])
@login_required
def create_doctor():
    if current_user.role != 'admin':
        flash('Unauthorized', 'danger')
        return redirect(url_for('home'))
    departments = Department.query.all()
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        full_name = request.form.get('full_name', '').strip()
        specialization = request.form.get('specialization', '').strip()
        dept_id = request.form.get('department_id')
        if User.query.filter_by(username=username).first():
            flash('Username exists', 'danger')
            return redirect(url_for('create_doctor'))
        user = User(username=username, role='doctor', full_name=full_name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        doc = Doctor(user_id=user.id, specialization=specialization, availability_json=json.dumps({}),
                     department_id=int(dept_id) if dept_id else None)
        db.session.add(doc)
        db.session.commit()
        flash('Doctor created', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('create_doctor.html', departments=departments)


@app.route('/admin/toggle_active/<int:user_id>', methods=['POST'])
@login_required
def admin_toggle_active(user_id):
    if current_user.role != 'admin':
        flash('Unauthorized', 'danger')
        return redirect(url_for('home'))
    user = User.query.get_or_404(user_id)
    user.active = not bool(user.active)
    db.session.commit()
    flash(f'User {user.username} {"activated" if user.active else "blacklisted"}', 'success')
    return redirect(request.referrer or url_for('admin_dashboard'))


@app.route('/admin/search', methods=['GET'])
@login_required
def admin_search():
    if current_user.role != 'admin':
        flash('Unauthorized', 'danger')
        return redirect(url_for('home'))
    q = request.args.get('q', '').strip()
    type_ = request.args.get('type', 'patient')  # 'patient' or 'doctor'
    results = []
    if type_ == 'patient':
        query = Patient.query.join(User)
        if q:
            if q.isdigit():
                query = query.filter(Patient.id == int(q))
            else:
                query = query.filter(User.full_name.ilike(f'%{q}%') | User.username.ilike(f'%{q}%') | User.contact.ilike(f'%{q}%'))
        results = query.all()
    else:
        query = Doctor.query.join(User)
        if q:
            query = query.filter(User.full_name.ilike(f'%{q}%') | User.username.ilike(f'%{q}%') | Doctor.specialization.ilike(f'%{q}%'))
        results = query.all()
    return render_template('admin_search.html', results=results, q=q, type_=type_)


# -------------------------
# Doctor routes
# -------------------------
@app.route('/doctor')
@login_required
def doctor_dashboard():
    if current_user.role != 'doctor':
        flash('Unauthorized', 'danger')
        return redirect(url_for('home'))
    doc = Doctor.query.filter_by(user_id=current_user.id).first()
    today = date.today()
    upcoming = Appointment.query.filter_by(doctor_id=doc.id).filter(Appointment.date >= today).order_by(Appointment.date).all()
    # show assigned patients
    assigned_patients = {a.patient.user.full_name: a.patient for a in upcoming}
    return render_template('doctor_dashboard.html', doc=doc, upcoming=upcoming, patients=assigned_patients)


@app.route('/doctor/availability', methods=['GET', 'POST'])
@login_required
def doctor_availability():
    if current_user.role != 'doctor':
        flash('Unauthorized', 'danger')
        return redirect(url_for('home'))
    doc = Doctor.query.filter_by(user_id=current_user.id).first()
    availability = {}
    if doc and doc.availability_json:
        try:
            availability = json.loads(doc.availability_json)
        except:
            availability = {}
    dates = next_n_dates(7)
    if request.method == 'POST':
        updated = {}
        for d in dates:
            key = f"slots-{d.isoformat()}"
            val = request.form.get(key, '').strip()
            slots = [t.strip() for t in val.split(',') if t.strip()]
            if slots:
                updated[d.isoformat()] = slots
        doc.availability_json = json.dumps(updated)
        db.session.commit()
        flash('Availability updated', 'success')
        return redirect(url_for('doctor_dashboard'))
    return render_template('doctor_availability.html', availability=availability, dates=dates)


@app.route('/doctor/appointment/<int:appt_id>/treat', methods=['GET', 'POST'])
@login_required
def treat_appointment(appt_id):
    if current_user.role != 'doctor':
        flash('Unauthorized', 'danger')
        return redirect(url_for('home'))
    appt = Appointment.query.get_or_404(appt_id)
    doc = Doctor.query.filter_by(user_id=current_user.id).first()
    if appt.doctor_id != doc.id:
        flash('Unauthorized', 'danger')
        return redirect(url_for('doctor_dashboard'))
    if request.method == 'POST':
        diagnosis = request.form.get('diagnosis', '').strip()
        prescription = request.form.get('prescription', '').strip()
        notes = request.form.get('notes', '').strip()
        t = Treatment(appointment_id=appt.id, diagnosis=diagnosis, prescription=prescription, notes=notes)
        appt.status = 'Completed'
        db.session.add(t)
        db.session.commit()
        flash('Treatment saved and appointment marked Completed', 'success')
        return redirect(url_for('doctor_dashboard'))
    return render_template('treat_appointment.html', appt=appt)


@app.route('/doctor/appointment/<int:appt_id>/status', methods=['POST'])
@login_required
def doctor_update_status(appt_id):
    if current_user.role != 'doctor':
        flash('Unauthorized', 'danger')
        return redirect(url_for('home'))
    appt = Appointment.query.get_or_404(appt_id)
    doc = Doctor.query.filter_by(user_id=current_user.id).first()
    if appt.doctor_id != doc.id:
        flash('Unauthorized', 'danger')
        return redirect(url_for('doctor_dashboard'))
    new_status = request.form.get('status')
    if new_status in ['Booked', 'Completed', 'Cancelled']:
        appt.status = new_status
        db.session.commit()
        flash('Status updated', 'success')
    return redirect(request.referrer or url_for('doctor_dashboard'))


# -------------------------
# Patient routes
# -------------------------
@app.route('/patient')
@login_required
def patient_dashboard():
    if current_user.role != 'patient':
        flash('Unauthorized', 'danger')
        return redirect(url_for('home'))
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    doctors = Doctor.query.all()
    appointments = Appointment.query.filter_by(patient_id=patient.id).order_by(Appointment.date.desc()).all()
    return render_template('patient_dashboard.html', patient=patient, doctors=doctors, appointments=appointments)


@app.route('/doctor/<int:doctor_id>', methods=['GET', 'POST'])
@login_required
def doctor_profile(doctor_id):
    # Used by patients to view a doctor's profile and book
    doc = Doctor.query.get_or_404(doctor_id)
    availability = {}
    if doc.availability_json:
        try:
            availability = json.loads(doc.availability_json)
        except:
            availability = {}
    if request.method == 'POST':
        if current_user.role != 'patient':
            flash('Only patients can book', 'danger')
            return redirect(url_for('home'))
        patient = Patient.query.filter_by(user_id=current_user.id).first()
        date_str = request.form.get('date')
        time_str = request.form.get('time')
        try:
            appt_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except:
            flash('Invalid date', 'danger')
            return redirect(request.referrer or url_for('doctor_profile', doctor_id=doctor_id))
        # check doctor active
        if not doc.user.active:
            flash('Doctor is not available', 'danger')
            return redirect(url_for('patient_dashboard'))
        # ensure time exists in availability if availability is provided for that date
        if availability.get(date_str):
            if time_str not in availability.get(date_str):
                flash('Selected time not available for this doctor', 'danger')
                return redirect(url_for('doctor_profile', doctor_id=doctor_id))
        # create appointment and handle unique constraint
        from sqlalchemy.exc import IntegrityError
        appt = Appointment(patient_id=patient.id, doctor_id=doc.id, date=appt_date, time=time_str)
        db.session.add(appt)
        try:
            db.session.commit()
            flash('Appointment booked', 'success')
            return redirect(url_for('patient_dashboard'))
        except IntegrityError:
            db.session.rollback()
            flash('Selected slot already taken. Choose another time.', 'danger')
            return redirect(url_for('doctor_profile', doctor_id=doctor_id))
    return render_template('doctor_profile.html', doctor=doc, availability=availability, next7=next_n_dates(7))


@app.route('/appointment/<int:appt_id>/cancel', methods=['POST'])
@login_required
def cancel_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    # allow patient or doctor or admin to cancel
    if current_user.role == 'patient' and appt.patient.user_id != current_user.id:
        flash('Unauthorized', 'danger'); return redirect(url_for('home'))
    if current_user.role == 'doctor' and appt.doctor.user_id != current_user.id:
        flash('Unauthorized', 'danger'); return redirect(url_for('home'))
    appt.status = 'Cancelled'
    db.session.commit()
    flash('Appointment cancelled', 'success')
    return redirect(request.referrer or url_for('home'))


@app.route('/patient/history')
@login_required
def patient_history():
    if current_user.role != 'patient':
        flash('Unauthorized', 'danger'); return redirect(url_for('home'))
    patient = Patient.query.filter_by(user_id=current_user.id).first()
    appointments = Appointment.query.filter_by(patient_id=patient.id).order_by(Appointment.date.desc()).all()
    return render_template('appointment_history.html', appointments=appointments)


# -------------------------
# Search (Patients & Doctors for patients)
# -------------------------
@app.route('/search/doctors', methods=['GET'])
@login_required
def search_doctors():
    q = request.args.get('q', '').strip()
    dept = request.args.get('dept', '').strip()
    query = Doctor.query.join(User)
    if q:
        query = query.filter((User.full_name.ilike(f'%{q}%')) | (User.username.ilike(f'%{q}%')))
    if dept:
        query = query.filter(Doctor.specialization.ilike(f'%{dept}%') | Doctor.department.has(Department.name.ilike(f'%{dept}%')))
    results = query.all()
    return render_template('search_doctors.html', doctors=results, q=q, dept=dept)


@app.route('/search/patients', methods=['GET'])
@login_required
def search_patients():
    if current_user.role != 'admin':
        flash('Unauthorized', 'danger'); return redirect(url_for('home'))
    q = request.args.get('q','').strip()
    query = Patient.query.join(User)
    if q:
        query = query.filter((User.full_name.ilike(f'%{q}%')) | (User.username.ilike(f'%{q}%')) | (Patient.id == q))
    results = query.all()
    return render_template('search_patients.html', patients=results, q=q)


# -------------------------
# Simple JSON APIs (optional usage)
# -------------------------
@app.route('/api/doctors', methods=['GET'])
def api_doctors():
    docs = Doctor.query.all()
    out = []
    for d in docs:
        out.append({
            'id': d.id,
            'name': d.user.full_name,
            'username': d.user.username,
            'specialization': d.specialization,
            'department': d.department.name if d.department else None
        })
    return jsonify({'doctors': out})


@app.route('/api/patients', methods=['GET'])
def api_patients():
    patients = Patient.query.all()
    return jsonify({'patients': [{'id': p.id, 'name': p.user.full_name, 'username': p.user.username} for p in patients]})


@app.route('/api/appointments', methods=['GET', 'POST'])
def api_appointments():
    if request.method == 'GET':
        appts = Appointment.query.all()
        def ap(a):
            return {'id': a.id, 'doctor': a.doctor.user.full_name, 'patient': a.patient.user.full_name, 'date': a.date.isoformat(), 'time': a.time, 'status': a.status}
        return jsonify({'appointments': [ap(a) for a in appts]})
    data = request.get_json()
    if not data:
        return jsonify({'error':'JSON body required'}), 400
    try:
        appt_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        appt = Appointment(patient_id=int(data['patient_id']), doctor_id=int(data['doctor_id']), date=appt_date, time=data['time'])
        db.session.add(appt)
        db.session.commit()
        return jsonify({'status':'created', 'id': appt.id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400


# -------------------------
# Run
# -------------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_default_data()  # ensures predefined admin exists
    app.run(debug=True)

