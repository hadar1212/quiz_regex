from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import random
import json
from pymongo import MongoClient
from datetime import datetime
import uuid
import os

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')

# MongoDB connection
MONGO_URL = "mongodb+srv://muchtar1:muchtar1@cluster0.0r9vavw.mongodb.net/regex-quiz?retryWrites=true&w=majority&appName=Cluster0"
client = MongoClient(MONGO_URL)
db = client['regex-quiz']
collection = db['regex_quiz_results']

def load_regex_data_from_file(filename="regex_test_data_fixed.json"):
    """
    Load regex data from a JSON file.
    If you have a larger database file, replace the hardcoded data above.
    """
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"File {filename} not found, using hardcoded data")
        return {}

def generate_quiz():
    """Generate a quiz with 10 expressions (20 questions total)"""
    # Use the hardcoded data or load from file
    data = load_regex_data_from_file()  # TODO to load_regex_data_from_file()

    # Select 10 random expressions (or all if less than 10 available)
    selected_expressions = random.sample(data, min(10, len(data)))

    questions = []

    for expr in selected_expressions:
        # Add regex question
        if random.random() < 0.5:
            # Select correct answer (50% probability)
            answer = random.choice(expr['5_correct_answers'])
            correct = True
        else:
            # Select incorrect answer (50% probability)
            answer = random.choice(expr['5_incorrect_answers'])
            correct = False

        questions.append({
            'regex': expr['regex'],
            'test_string': answer,
            'correct_answer': correct,
            'type': 'original_regex'
        })

        # Add refactor_regex question
        if random.random() < 0.5:
            # Select correct answer (50% probability)
            answer = random.choice(expr['5_correct_answers'])
            correct = True
        else:
            # Select incorrect answer (50% probability)
            answer = random.choice(expr['5_incorrect_answers'])
            correct = False

        questions.append({
            'regex': expr['refactor_regex'],
            'test_string': answer,
            'correct_answer': correct,
            'type': 'refactor_regex'
        })

    # Shuffle questions for random order
    random.shuffle(questions)
    return questions

# ===== NEW ONBOARDING ROUTES =====

@app.route('/')
def consent():
    """Consent form page - Step 1"""
    return render_template('consent.html')

@app.route('/rules')
def rules():
    """Regex rules page - Step 2"""
    return render_template('rules.html')

@app.route('/sample')
def sample():
    """Sample question page - Step 3"""
    # Use a specific sample question (or get from your JSON data)
    sample_question = {
        "regex": r"([\w]*):\/\/(.*)",
        "test_string": "https://example.com",
        "correct_answer": True
    }
    return render_template('sample.html', question=sample_question)

@app.route('/ready')
def ready():
    """Ready to start page - Step 4"""
    return render_template('ready.html')

# ===== MODIFIED EXISTING ROUTES =====

@app.route('/index')
def index():
    """Legacy route - redirect to consent"""
    return redirect(url_for('consent'))

@app.route('/start-quiz')  # Changed from /start_quiz to match new template
def start_quiz():
    """Initialize a new quiz session - Your existing logic"""
    quiz_questions = generate_quiz()
    session['quiz_id'] = str(uuid.uuid4())
    session['quiz_questions'] = quiz_questions
    session['current_question'] = 0
    session['user_answers'] = []  # Keep for backwards compatibility
    session['detailed_answers'] = []  # New detailed format
    session['start_time'] = datetime.utcnow().isoformat()

    return redirect(url_for('quiz'))

@app.route('/quiz')
def quiz():
    """Display current quiz question - Your existing logic with small modification"""
    if 'quiz_questions' not in session:
        return redirect(url_for('consent'))  # Changed from index to consent

    current_q = session['current_question']
    questions = session['quiz_questions']

    if current_q >= len(questions):
        return redirect(url_for('results'))

    question = questions[current_q]
    return render_template('quiz.html',
                           question=question,
                           question_num=current_q + 1,
                           total_questions=len(questions))

@app.route('/answer', methods=['POST'])
def answer():
    """Process user's answer - Your existing logic unchanged"""
    if 'quiz_questions' not in session:
        return jsonify({'error': 'No active quiz'}), 400

    user_answer = request.json.get('answer')  # True or False
    time_to_answer = request.json.get('time_to_answer', 0)  # Time in seconds
    current_q = session['current_question']
    questions = session['quiz_questions']

    if current_q >= len(questions):
        return jsonify({'error': 'Quiz completed'}), 400

    question = questions[current_q]
    is_correct = (user_answer == question['correct_answer'])

    # Store the answer with new format
    answer_data = {
        'test_number': current_q + 1,  # Question ID (1-20)
        'regex': question['regex'],
        'is_regex_refactor': 'YES' if question['type'] == 'refactor_regex' else 'NO',
        'user_answer': user_answer,
        'is_correct': 'YES' if is_correct else 'NO',
        'time_to_answer': time_to_answer
    }

    # Store in session
    if 'detailed_answers' not in session:
        session['detailed_answers'] = []
    session['detailed_answers'].append(answer_data)

    session['current_question'] += 1

    # Return only whether there's a next question - NO correct/incorrect info
    return jsonify({
        'next_question': session['current_question'] < len(questions)
    })

@app.route('/results')
def results():
    """Display quiz results and save to MongoDB - Your existing logic unchanged"""
    if 'detailed_answers' not in session:
        return redirect(url_for('consent'))  # Changed from index to consent

    detailed_answers = session['detailed_answers']
    correct_count = sum(1 for answer in detailed_answers if answer['is_correct'] == 'YES')
    total_questions = len(detailed_answers)
    score = (correct_count / total_questions) * 100 if total_questions > 0 else 0

    # Calculate completion time
    start_time = datetime.fromisoformat(session.get('start_time', datetime.utcnow().isoformat()))
    end_time = datetime.utcnow()
    duration_seconds = (end_time - start_time).total_seconds()

    # Store results in MongoDB with new format
    quiz_result = {
        'quiz_id': session.get('quiz_id'),
        'start_time': start_time,
        'end_time': end_time,
        'total_duration_seconds': duration_seconds,
        'total_questions': total_questions,
        'correct_answers': correct_count,
        'score_percentage': score,
        'detailed_answers': detailed_answers  # New detailed format with timing
    }

    try:
        result = collection.insert_one(quiz_result)
        print(f"Quiz result saved to MongoDB with ID: {result.inserted_id}")
        print(f"Stored {len(detailed_answers)} detailed answers with timing data")
    except Exception as e:
        print(f"Error storing results in MongoDB: {e}")

    return render_template('results.html',
                           correct_count=correct_count,
                           total_questions=total_questions,
                           score=score,
                           duration=duration_seconds,
                           answers=detailed_answers)

@app.route('/restart')
def restart():
    """Clear session and restart quiz"""
    session.clear()
    return redirect(url_for('consent'))

# Health check route for deployment
@app.route('/health')
def health():
    return {'status': 'healthy'}, 200

if __name__ == '__main__':
    # Production-ready settings
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)