from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import random
import json
from pymongo import MongoClient
from datetime import datetime
import uuid
import os
import ssl

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')

# MongoDB connection with SSL fix
MONGO_URL = "mongodb+srv://muchtar1:muchtar1@cluster0.0r9vavw.mongodb.net/regex-quiz?retryWrites=true&w=majority&appName=Cluster0"


def get_mongo_client():
    """Create MongoDB client with proper SSL settings"""
    try:
        # For MongoDB Atlas, ssl is automatically handled with mongodb+srv://
        # No need for manual SSL configuration
        client = MongoClient(
            MONGO_URL,
            serverSelectionTimeoutMS=5000,  # 5 second timeout
            connectTimeoutMS=5000,
            socketTimeoutMS=5000,
            retryWrites=True
        )
        # Test the connection
        client.admin.command('ping')
        return client
    except Exception as e:
        print(f"MongoDB connection failed: {e}")
        return None


# Try to connect to MongoDB
client = get_mongo_client()
if client:
    db = client['regex-quiz']
    quiz_answers_collection = db['quiz_answers']  # Single collection only
    print("✅ MongoDB connected successfully")
else:
    print("❌ MongoDB connection failed - running without database")
    db = None
    quiz_answers_collection = None


def initialize_quiz_document(quiz_id, questions):
    """Create ONE document with array of all questions initialized with null values"""
    if quiz_answers_collection is None:
        return False

    try:
        # Create array of answer objects, all initialized with null
        answers_array = []
        for i, question in enumerate(questions):
            answer_record = {
                'test_number': i + 1,
                'regex': question['regex'],
                'test_string': question['test_string'],
                'correct_answer': question['correct_answer'],
                'is_regex_refactor': 'YES' if question['type'] == 'refactor_regex' else 'NO',
                'user_answer': None,  # Start with null
                'is_correct': None,  # Start with null
                'time_to_answer': None,  # Start with null
                'skipped': None,  # Start with null
                'question_status': 'not_reached'  # Status: not_reached, answered, skipped
            }
            answers_array.append(answer_record)

        # Create the main quiz document
        quiz_document = {
            'quiz_id': quiz_id,
            'start_time': datetime.utcnow(),
            'end_time': None,
            'total_duration_seconds': None,
            'total_questions': len(questions),
            'answered_questions': 0,
            'skipped_questions': 0,
            'unanswered_questions': len(questions),  # Initially all unanswered
            'correct_answers': 0,
            'score_percentage': 0,
            'average_time_per_question': 0,  # New field for average time
            'status': 'in_progress',
            'answers': answers_array,  # All answers in one array
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }

        # Insert the document
        result = quiz_answers_collection.insert_one(quiz_document)
        print(f"✅ Initialized quiz document for {quiz_id} with {len(questions)} questions")
        return True

    except Exception as e:
        print(f"❌ Error initializing quiz document: {e}")
        return False


def update_answer_in_document(quiz_id, test_number, answer_data):
    """Update specific answer in the answers array AND update summary statistics"""
    if quiz_answers_collection is None:
        return False

    try:
        # First, update the specific answer in the array
        update_query = {
            '$set': {
                f'answers.{test_number - 1}.user_answer': answer_data.get('user_answer'),
                f'answers.{test_number - 1}.is_correct': answer_data.get('is_correct'),
                f'answers.{test_number - 1}.time_to_answer': answer_data.get('time_to_answer'),
                f'answers.{test_number - 1}.skipped': answer_data.get('skipped', False),
                f'answers.{test_number - 1}.question_status': answer_data.get('question_status'),
                'updated_at': datetime.utcnow()
            }
        }

        result = quiz_answers_collection.update_one(
            {'quiz_id': quiz_id},
            update_query
        )

        if result.modified_count > 0:
            # Now recalculate and update summary statistics
            document = quiz_answers_collection.find_one({'quiz_id': quiz_id})
            if document:
                answers = document.get('answers', [])

                # Count current statistics from the answers array
                answered_count = sum(1 for a in answers if a.get('question_status') == 'answered')
                skipped_count = sum(1 for a in answers if a.get('question_status') == 'skipped')
                not_reached_count = sum(1 for a in answers if a.get('question_status') == 'not_reached')
                correct_count = sum(1 for a in answers if a.get('is_correct') == 'YES')

                # Calculate average time per question (only for answered questions)
                total_time = sum(a.get('time_to_answer', 0) for a in answers if
                                 a.get('question_status') == 'answered' and a.get('time_to_answer') is not None)
                avg_time = round(total_time / answered_count, 2) if answered_count > 0 else 0

                # Calculate score based on answered questions only (excluding skipped)
                score = round((correct_count / answered_count * 100), 2) if answered_count > 0 else 0

                # Update summary statistics in real-time
                summary_update = {
                    '$set': {
                        'answered_questions': answered_count,
                        'skipped_questions': skipped_count,
                        'unanswered_questions': not_reached_count,
                        'correct_answers': correct_count,  # ← Updates in real-time!
                        'score_percentage': score,  # ← Updates in real-time!
                        'average_time_per_question': avg_time,  # ← New real-time average!
                        'updated_at': datetime.utcnow()
                    }
                }

                quiz_answers_collection.update_one(
                    {'quiz_id': quiz_id},
                    summary_update
                )

                print(f"✅ Updated answer for quiz {quiz_id}, question {test_number}")
                print(
                    f"📊 Real-time Stats: {answered_count} answered, {skipped_count} skipped, {not_reached_count} unanswered")
                print(f"🎯 Score: {correct_count}/{answered_count} correct = {score}%")
                print(f"⏱️ Average time per question: {avg_time}s")
                return True
            else:
                print(f"⚠️ Could not find document to update stats for quiz {quiz_id}")
                return False
        else:
            print(f"⚠️ No document found to update for quiz {quiz_id}")
            return False

    except Exception as e:
        print(f"❌ Error updating answer: {e}")
        return False


def finalize_quiz_document(quiz_id, session_data):
    """Update final quiz statistics in the document"""
    if quiz_answers_collection is None:
        return False

    try:
        # Calculate final statistics
        detailed_answers = session_data.get('detailed_answers', [])
        start_time = datetime.fromisoformat(session_data.get('start_time', datetime.utcnow().isoformat()))
        end_time = datetime.utcnow()
        duration_seconds = (end_time - start_time).total_seconds()

        correct_count = sum(1 for answer in detailed_answers if answer.get('is_correct') == 'YES')
        skipped_count = sum(1 for answer in detailed_answers if answer.get('skipped', False))
        answered_count = len(detailed_answers) - skipped_count
        total_questions = len(session_data.get('quiz_questions', []))
        unanswered_count = total_questions - len(detailed_answers)

        score = (correct_count / answered_count * 100) if answered_count > 0 else 0

        # Calculate average time per question for final summary
        total_time = sum(answer.get('time_to_answer', 0) for answer in detailed_answers if
                         not answer.get('skipped', False) and answer.get('time_to_answer') is not None)
        avg_time = round(total_time / answered_count, 2) if answered_count > 0 else 0

        # Update final statistics
        update_query = {
            '$set': {
                'end_time': end_time,
                'total_duration_seconds': duration_seconds,
                'answered_questions': answered_count,
                'skipped_questions': skipped_count,
                'unanswered_questions': unanswered_count,
                'correct_answers': correct_count,
                'score_percentage': score,
                'average_time_per_question': avg_time,  # Final average time
                'status': 'completed' if unanswered_count == 0 else 'incomplete',
                'updated_at': datetime.utcnow()
            }
        }

        result = quiz_answers_collection.update_one(
            {'quiz_id': quiz_id},
            update_query
        )

        if result.modified_count > 0:
            print(f"✅ Finalized quiz document for {quiz_id}")
            return True
        else:
            print(f"⚠️ No document found to finalize for quiz {quiz_id}")
            return False

    except Exception as e:
        print(f"❌ Error finalizing quiz document: {e}")
        return False


def get_unanswered_questions_from_document(quiz_id):
    """Get unanswered questions from the document"""
    if quiz_answers_collection is None:
        return 0, []

    try:
        document = quiz_answers_collection.find_one({'quiz_id': quiz_id})
        if not document:
            return 0, []

        unanswered_questions = []
        for answer in document.get('answers', []):
            if answer.get('question_status') == 'not_reached':
                unanswered_questions.append(answer.get('test_number'))

        unanswered_questions.sort()
        return len(unanswered_questions), unanswered_questions

    except Exception as e:
        print(f"❌ Error getting unanswered questions: {e}")
        return 0, []


def load_regex_data_from_file(filename="regex_test_data_fixed.json"):
    """
    Load regex data from a JSON file.
    """
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"File {filename} not found, using fallback data")
        # Fallback data if file not found
        return []


def generate_quiz():
    """Generate a quiz with 8 expressions (16 questions total)"""
    data = load_regex_data_from_file()

    # Select 8 random expressions (or all if less than 8 available)
    selected_expressions = random.sample(data, min(8, len(data)))

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


# ===== ONBOARDING ROUTES =====

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


# ===== QUIZ ROUTES =====

@app.route('/index')
def index():
    """Legacy route - redirect to consent"""
    return redirect(url_for('consent'))


@app.route('/start-quiz')
def start_quiz():
    """Initialize a new quiz session with single document setup"""
    quiz_questions = generate_quiz()
    quiz_id = str(uuid.uuid4())

    # Initialize quiz document with all questions as null
    initialize_quiz_document(quiz_id, quiz_questions)

    session['quiz_id'] = quiz_id
    session['quiz_questions'] = quiz_questions
    session['current_question'] = 0
    session['user_answers'] = []
    session['detailed_answers'] = []
    session['start_time'] = datetime.utcnow().isoformat()

    print(f"🚀 Started quiz {quiz_id} with {len(quiz_questions)} questions")
    return redirect(url_for('quiz'))


@app.route('/quiz')
def quiz():
    """Display current quiz question"""
    if 'quiz_questions' not in session:
        return redirect(url_for('consent'))

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
    """Process user's answer or skip and update document in real-time"""
    if 'quiz_questions' not in session:
        return jsonify({'error': 'No active quiz'}), 400

    user_answer = request.json.get('answer')
    time_to_answer = request.json.get('time_to_answer', 0)
    is_skipped = request.json.get('skipped', False)
    current_q = session['current_question']
    questions = session['quiz_questions']
    quiz_id = session.get('quiz_id')

    if current_q >= len(questions):
        return jsonify({'error': 'Quiz completed'}), 400

    question = questions[current_q]
    test_number = current_q + 1

    # Handle skipped questions
    if is_skipped:
        answer_data = {
            'test_number': test_number,
            'regex': question['regex'],
            'is_regex_refactor': 'YES' if question['type'] == 'refactor_regex' else 'NO',
            'user_answer': None,  # Keep as null for skipped
            'is_correct': 'SKIPPED',
            'time_to_answer': time_to_answer,
            'skipped': True,
            'question_status': 'skipped'
        }
    else:
        # Handle normal answers
        is_correct = (user_answer == question['correct_answer'])
        answer_data = {
            'test_number': test_number,
            'regex': question['regex'],
            'is_regex_refactor': 'YES' if question['type'] == 'refactor_regex' else 'NO',
            'user_answer': user_answer,
            'is_correct': 'YES' if is_correct else 'NO',
            'time_to_answer': time_to_answer,
            'skipped': False,
            'question_status': 'answered'
        }

    # Update document in real-time
    update_success = update_answer_in_document(quiz_id, test_number, answer_data)

    if not update_success:
        print(f"⚠️ Failed to update document for question {test_number}")

    # Keep in session for backward compatibility
    if 'detailed_answers' not in session:
        session['detailed_answers'] = []
    session['detailed_answers'].append(answer_data)

    session['current_question'] += 1

    return jsonify({
        'next_question': session['current_question'] < len(questions),
        'updated': update_success
    })


@app.route('/results')
def results():
    """Display quiz results and finalize the document"""
    if 'detailed_answers' not in session:
        return redirect(url_for('consent'))

    detailed_answers = session['detailed_answers']
    quiz_id = session.get('quiz_id')

    # Calculate statistics
    correct_count = sum(1 for answer in detailed_answers if answer.get('is_correct') == 'YES')
    skipped_count = sum(1 for answer in detailed_answers if answer.get('skipped', False))
    answered_count = len(detailed_answers) - skipped_count
    total_questions = len(session.get('quiz_questions', []))

    # Calculate score based on answered questions only
    score = (correct_count / answered_count * 100) if answered_count > 0 else 0

    # Calculate completion time
    start_time = datetime.fromisoformat(session.get('start_time', datetime.utcnow().isoformat()))
    end_time = datetime.utcnow()
    duration_seconds = (end_time - start_time).total_seconds()

    # Get unanswered questions from document
    unanswered_count, unanswered_questions = get_unanswered_questions_from_document(quiz_id)

    # Calculate average time per question for display
    total_answered_time = sum(answer.get('time_to_answer', 0) for answer in detailed_answers if
                              not answer.get('skipped', False) and answer.get('time_to_answer') is not None)
    avg_time_per_question = round(total_answered_time / answered_count, 1) if answered_count > 0 else 0

    # Finalize the quiz document with final statistics
    finalize_success = finalize_quiz_document(quiz_id, session)

    if finalize_success:
        print(
            f"✅ Quiz {quiz_id} finalized: {answered_count} answered, {skipped_count} skipped, {unanswered_count} unanswered")
    else:
        print(f"⚠️ Failed to finalize quiz document for {quiz_id}")

    return render_template('results.html',
                           correct_count=correct_count,
                           total_questions=total_questions,
                           score=score,
                           duration=duration_seconds,
                           answers=detailed_answers,
                           saved_to_db=finalize_success,
                           skipped_count=skipped_count,
                           unanswered_count=unanswered_count,
                           unanswered_questions=unanswered_questions,
                           avg_time_per_question=avg_time_per_question)


@app.route('/restart')
def restart():
    """Clear session and restart quiz"""
    session.clear()
    return redirect(url_for('consent'))


@app.route('/health')
def health():
    """Health check for deployment"""
    mongo_status = "connected" if quiz_answers_collection is not None else "disconnected"
    return {
        'status': 'healthy',
        'mongodb': mongo_status
    }, 200


@app.route('/fix-missing-fields')
def fix_missing_fields():
    """Add missing average_time_per_question field to existing documents"""
    if quiz_answers_collection is None:
        return jsonify({'error': 'Database not available'}), 500

    try:
        # Find documents missing the field
        result = quiz_answers_collection.update_many(
            {"average_time_per_question": {"$exists": False}},
            {"$set": {"average_time_per_question": 0}}
        )

        return jsonify({
            'status': 'success',
            'message': f'Added average_time_per_question field to {result.modified_count} documents',
            'modified_count': result.modified_count
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/debug-quiz/<quiz_id>')
def debug_quiz(quiz_id):
    """Debug endpoint to see the quiz document"""
    if quiz_answers_collection is None:
        return jsonify({'error': 'Database not available'}), 500

    try:
        # Get the quiz document
        document = quiz_answers_collection.find_one({'quiz_id': quiz_id}, {'_id': 0})

        if not document:
            return jsonify({'error': 'Quiz not found'}), 404

        # Count different statuses from answers array
        answers = document.get('answers', [])
        answered = sum(1 for a in answers if a.get('question_status') == 'answered')
        skipped = sum(1 for a in answers if a.get('question_status') == 'skipped')
        not_reached = sum(1 for a in answers if a.get('question_status') == 'not_reached')

        # Add summary to response
        document['summary'] = {
            'answered_questions': answered,
            'skipped_questions': skipped,
            'unanswered_questions': not_reached,
            'average_time_per_question': document.get('average_time_per_question', 0)
        }

        return jsonify(document)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)