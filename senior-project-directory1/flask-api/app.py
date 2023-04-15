from flask import Flask, request, session
from models import db, Student, Degree_Plan, Course, Courses_Needed, Course_Offering, Conflict, Class_Choices, Course_History
from flask_jwt_extended import JWTManager, create_access_token, create_refresh_token, jwt_required, get_jwt_identity
from flask import jsonify
from flask_cors import CORS
from scheduler import Scheduler
from convert_course_data import convert_course_data, process_conflict_string
from sqlalchemy.sql import text


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:N00k!e99123@localhost:5432/course_model'
app.config['FLASK APP'] = app
app.config['SECRET_KEY'] = '1d387a4ec8206070645d8c87'
db.init_app(app)
jwt = JWTManager(app)
CORS(app)

#test api route
@app.route('/helloworld', methods =["GET"])
def helloworld():
    return jsonify({'message': 'hey'})

#registration route to add user to database
@app.route('/register', methods = ['POST'])
def register():
    #get email and password from http request (change this to request.json when integrating front end, args is only for testing)
    email = request.json.get('email')
    password = request.json.get('password')
    major = request.json.get('major')
    #check if user exists
    if db.session.query(Student).filter_by(email = email).first():
        return jsonify({'message':'User exists! Please try another email or login.'})
    #get dp_id from degree_plan table to insert in student table as major
    major_id = db.session.query(Degree_Plan.dp_id).filter(Degree_Plan.dpt_code == major).scalar()
    #create new student object to add all properties
    student = Student(email = email,  
                      major = major_id
                      )
    student.password_hash = student.set_password(password)
    #commit student to database
    db.session.add_all([student])
    db.session.commit()
    #not sure if this is the correct thing to return
    return jsonify({'message':'User has been registered successfully!'})

#login route to verify username and password in the database
@app.route('/login', methods = ['POST'])
def login():
    #get email and password from http request 
    email = request.json.get('email')
    password = request.json.get('password')
    #if email is not in json data return an error (should probably just check this on the front end before sending)
    if not email or not password:
        return jsonify({'message':'Please enter an email or password'})
    #pull occurrence of user out of the database
    student = db.session.query(Student).filter_by(email = email).first()
    #verify password hash, if invalid return invalid
    if  not student or not student.check_password(password):
        return jsonify({'message':'invalid email or password'})
    #create a json web token for user to access private pages
    access_token = create_access_token(identity = email)
    refresh_token = create_refresh_token(identity = email)
    session['stuid'] = student.stuid
    session['major'] = student.major
    #if user is valid provide token to access private pages
    return jsonify({"access_token": access_token, "refresh_token": refresh_token})

#destroy session token
@app.route('/logout')
def logout():
    session.clear()  # Clear the entire session
    return jsonify({"message":"User has been logged out."})

#create a refrese token to keep user logged in, in case the auth token expires
@app.route('/refresh', methods = ["POST"])
@jwt_required(refresh = True)
def post():
    current_user = get_jwt_identity()
    new_access_token = create_access_token(identity=current_user)
    return jsonify({'access_token':new_access_token})

#route that gets all majors to populate the dropdown list
@app.route('/majors', methods=["GET"])
def get_majors():
    majors = db.session.query(Degree_Plan.dpt_code).all()
    major_list = [major[0] for major in majors]
    
    return jsonify(major_list)

#route for submitting the form
@app.route('/save_user_data', methods = ['POST'])
def save_data():
    data = request.json
    class_history = data['history']
    class_names = data['classes']
    conflicts_list = data['conflicts']
    
    hist = db.session.query(Course_History, Course)\
        .join(Course, Course_History.course_id == Course.course_id)\
            .filter(Course_History.stuid == session['stuid']).all()
    if class_history:
        for course in class_history:
            if course not in hist:
                cid = db.session.query(Course.course_id).filter(Course.name == course).one_or_none()
                item = Course_History(stuid = session['stuid'],course_id = cid[0])
                db.session.add(item)
                db.session.commit()
    if class_names:
        # Add new rows to the session
        db.session.query(Class_Choices).filter(Class_Choices.stuid == session['stuid']).delete(synchronize_session=False)
        for name in class_names:
            choice = Class_Choices(stuid=session['stuid'], course_name=name)
            db.session.add(choice)
            db.session.commit()
    db.session.query(Conflict).filter(Conflict.stuid == session['stuid']).delete(synchronize_session = False)
    if conflicts_list:
        processed_conflicts_list = [process_conflict_string(item) for item in conflicts_list]
        unavailable = [Conflict(stuid=session['stuid'], name="conflict",
                        start_time=conflict['start_time'], end_time=conflict['end_time'], day=conflict['day'])
               for conflict in processed_conflicts_list]
        db.session.add_all(unavailable)
        db.session.commit()

    return jsonify({"message":"Saved classes successfully!"})


#route for rendering the 
@app.route('/find_combinations', methods=['GET'])
def find_combinations():
    conflict_query = db.session.query(Conflict).filter(Conflict.stuid == session['stuid']).all()
    conflicts_list = [row.__dict__.copy() for row in conflict_query]
    # Remove the '_sa_instance_state' key from each dictionary
    for d in conflicts_list:
        d.pop('_sa_instance_state', None)
        d.pop('cid', None)
        d.pop('stuid', None)
        d.pop('name', None)
    
    class_query = db.session.query(Class_Choices.course_name).filter(Class_Choices.stuid == session['stuid']).all()
    class_names = [row[0] for row in class_query]
    if not class_query:
        return jsonify({'message':'user has no classes selected'})
    conflicts = {i+1: conflict for i, conflict in enumerate(conflicts_list)}
    print(conflicts)
    # Query the database for the courses
    course_data = []
    for class_name in class_names:
        course_data.extend(db.session.query(Course_Offering).filter_by(name=class_name).all())
    formatted_classes = convert_course_data(course_data)
    scheduler = Scheduler(formatted_classes, conflicts)
    valid_combos = scheduler.get_valid_combinations()
    
    #Construct the JSON response
    response_data = []
    for combo in valid_combos:
        crns = scheduler.generate_crns(valid_combos.index(combo), valid_combos)
        crn_list = [int(crn) for crn in crns.split(',')]
        rows = [course.__dict__ for course in course_data if course.crn in crn_list]
        for row in rows:
            row.pop('_sa_instance_state', None)
        response_data.append(rows)

    return jsonify(response_data)

#get classes belonging to users majors
@app.route('/get_major_classes', methods = ['GET'])
def get_classes():
    classes = db.session.query(Courses_Needed, Course)\
    .join(Course, Courses_Needed.course_id == Course.course_id)\
    .filter(Courses_Needed.dp_id == session['major'])\
    .all()
    
    names = [course.name for _, course in classes]
    
    return jsonify(names = names)
    
    
        
        
    

    
if __name__ == '__main__':
    app.run(debug = True)