from flask import Flask, render_template, request, redirect, session, flash
import pymysql
import requests
import urllib.parse
from openai import OpenAI
from werkzeug.security import generate_password_hash, check_password_hash
import os
from dotenv import load_dotenv
import base64
import uuid

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

def generate_links(item_name):
    query = urllib.parse.quote(item_name)
    return {
        "amazon": f"https://www.amazon.in/s?k={query}",
        "flipkart": f"https://www.flipkart.com/search?q={query}",
        "myntra": f"https://www.myntra.com/{query.replace('%20', '-')}",
        "ajio": f"https://www.ajio.com/search/?text={query}"
    }

app = Flask(__name__)
app.secret_key = "wearwise_secret_key"

db = pymysql.connect(
    host="localhost",
    user="root",
    password="",
    database="wearwise1",
    cursorclass=pymysql.cursors.DictCursor
)

# ================= LANDING =================
@app.route('/')
def landing():
    return render_template("landing.html")

# ================= REGISTER =================
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))

        if cursor.fetchone():
            flash("Username already exists")
            return redirect('/register')

        hashed = generate_password_hash(password)

        cursor.execute(
            "INSERT INTO users (username,password) VALUES (%s,%s)",
            (username,hashed)
        )
        db.commit()
        return redirect('/login')

    return render_template("register.html")

# ================= LOGIN =================
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cursor.fetchone()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['user_id']
            session['username'] = user['username']
            return redirect('/home')
        else:
            flash("Invalid login")
            return redirect('/login')

    return render_template("login.html")

# ================= HOME =================
@app.route('/home')
def home():
    if 'user_id' not in session:
        return redirect('/login')

    cursor = db.cursor()
    cursor.execute("SELECT occasion_name FROM occasions")
    occasions = cursor.fetchall()

    return render_template(
        "index.html",
        occasions=occasions,
        username=session.get('username')
    )

# ================= WEATHER =================
@app.route('/weather', methods=['POST'])
def weather():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']

    gender = request.form['gender']
    age = request.form['age']

    occasion = request.form.get('occasion', "").strip()
    new_occasion = request.form.get('new_occasion', "").strip()

    lat = request.form['lat']
    lon = request.form['lon']

    cursor = db.cursor()

    if occasion and new_occasion:
        flash("Please select either an existing occasion OR add a new one, not both.")
        return redirect('/home')

    if not occasion and not new_occasion:
        flash("Please select or enter an occasion.")
        return redirect('/home')

    if new_occasion:
        occasion = new_occasion.title()
        cursor.execute(
            "INSERT IGNORE INTO occasions (occasion_name) VALUES (%s)",
            (occasion,)
        )
        db.commit()

    cursor.execute("SELECT outfit_type, fabric FROM wardrobe WHERE user_id=%s", (user_id,))
    user_wardrobe = cursor.fetchall()
    
    wardrobe_context = ""
    if user_wardrobe:
        wardrobe_str = ", ".join([f"{item['fabric']} {item['outfit_type']}" for item in user_wardrobe])
        wardrobe_context = f"\nUser's Wardrobe (PRIORITIZE THESE IF APPROPRIATE FOR WEATHER/OCCASION): {wardrobe_str}\n"

    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric"
        data = requests.get(url).json()

        temperature = data['main']['temp']
        weather_condition = data['weather'][0]['main']
        city = data.get("name", "Your Place")
    except:
        temperature = 30
        weather_condition = "Clear"
        city = "Your Place"

    # ✅ UPDATED PROMPT
    prompt = f"""
You are a professional Indian fashion designer.

Generate a modest, stylish, and age-appropriate outfit recommendation.

STRICT FORMAT (NO *, NO EXTRA TEXT):
Top: ...
Bottom: ...
Footwear: ...
Accessories: ...

Guidelines:
- Age MUST be strictly followed: {age}
- Gender: {gender}
- Occasion: {occasion}
- Weather: {weather_condition}
- Temperature: {temperature}°C
- Prioritize wardrobe items if suitable
- Modern outfit with subtle Indian elements (NOT overly traditional)
- Avoid revealing styles
- Keep it elegant, realistic, and wearable in India

{wardrobe_context}
"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    recommendation = completion.choices[0].message.content.strip()

    # ✅ REMOVE ASTERISKS
    recommendation = recommendation.replace("*", "")

    outfit_dict = {}
    for line in recommendation.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            outfit_dict[key.strip()] = value.strip()

    items = []
    for value in outfit_dict.values():
        if value:
            items.append({
                "name": value,
                "links": generate_links(value)
            })

    # ✅ UPDATED IMAGE PROMPT
    image = client.images.generate(
        model="gpt-image-1",
        prompt=f"""
A high-quality fashion catalog image of an outfit WITHOUT any human model.

Outfit: {recommendation}

Display using mannequin or flat lay.

Guidelines:
- Designed by an Indian fashion designer
- Modern outfit with subtle Indian elements
- Not overly traditional
- Modest and elegant
- Age-appropriate
- Clean minimal background
- Suitable for {weather_condition} weather and {occasion}
- Avoid revealing clothing
""",
        size="1024x1024"
    )

    img_bytes = base64.b64decode(image.data[0].b64_json)

    folder = os.path.join(app.root_path, "static/generated")
    os.makedirs(folder, exist_ok=True)

    filename = str(uuid.uuid4()) + ".png"
    filepath = os.path.join(folder, filename)

    with open(filepath, "wb") as f:
        f.write(img_bytes)

    image_path = "/static/generated/" + filename

    cursor.execute("""
    INSERT INTO outfit_ideas (user_id, recommendation, outfit_image)
    VALUES (%s, %s, %s)
    """, (user_id, recommendation, image_path))

    db.commit()

    return render_template(
        "result.html",
        outfit=outfit_dict,
        image_url=image_path,
        city=city,
        temp=temperature,
        weather=weather_condition,
        items=items
    )

# ================= SAVED =================
@app.route('/saved')
def saved():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']
    cursor = db.cursor()

    cursor.execute("""
    SELECT recommendation, outfit_image
    FROM outfit_ideas
    WHERE user_id=%s
    """, (user_id,))

    outfits = cursor.fetchall()
    return render_template("saved.html", outfits=outfits)

# ================= WARDROBE =================
@app.route('/wardrobe', methods=['GET', 'POST'])
def wardrobe():
    if 'user_id' not in session:
        return redirect('/login')
        
    user_id = session['user_id']
    cursor = db.cursor()
    
    if request.method == 'POST':
        outfit_types = request.form.getlist('outfit_type[]')
        fabrics = request.form.getlist('fabric[]')
        images = request.files.getlist('image[]')
        
        folder = os.path.join(app.root_path, "static/wardrobe_uploads")
        os.makedirs(folder, exist_ok=True)
        
        for i in range(len(images)):
            img = images[i]
            if img and img.filename:
                filename = str(uuid.uuid4()) + "_" + img.filename
                filepath = os.path.join(folder, filename)
                img.save(filepath)
                
                image_path = "/static/wardrobe_uploads/" + filename
                o_type = outfit_types[i] if i < len(outfit_types) else ""
                fab = fabrics[i] if i < len(fabrics) else ""
                
                cursor.execute("""
                    INSERT INTO wardrobe (user_id, image_path, outfit_type, fabric) 
                    VALUES (%s, %s, %s, %s)
                """, (user_id, image_path, o_type, fab))
        
        db.commit()
        flash("Wardrobe updated successfully!", "success")
        return redirect('/wardrobe')

    cursor.execute("SELECT * FROM wardrobe WHERE user_id=%s", (user_id,))
    items = cursor.fetchall()
    return render_template("wardrobe.html", items=items)

# ================= LOGOUT =================
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# ================= ADMIN =================
from werkzeug.security import check_password_hash

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD_HASH = "scrypt:32768:8:1$SzxHAqGDsOPzS5yr$a2d17fe9f2acea81dd15d614d576265b0da338447cb56c4f41cbeb460cb3c1b5f0568acf81aa779e3c2985598485805da30b024779bb9799d0592d537e803352"

@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session['admin'] = True
            return redirect('/admin/dashboard')
        else:
            flash("Invalid admin credentials")
            return redirect('/admin/login')

    return render_template("admin_login.html")

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin' not in session:
        return redirect('/admin/login')

    cursor = db.cursor()

    cursor.execute("SELECT COUNT(*) AS total FROM users")
    total_users = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS total FROM wardrobe")
    total_wardrobe = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS total FROM outfit_ideas")
    total_outfits = cursor.fetchone()['total']

    cursor.execute("""
        SELECT recommendation, generated_date
        FROM outfit_ideas
        ORDER BY generated_date DESC
        LIMIT 5
    """)
    recent_outfits = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) AS total FROM feedback WHERE message IS NOT NULL AND message != ''")
    total_feedback = cursor.fetchone()['total']

    cursor.execute("SELECT recommendation FROM outfit_ideas")
    data = cursor.fetchall()

    occasion_count = {}
    for row in data:
        text = row['recommendation'].lower()

        if "party" in text:
            occasion_count["Party"] = occasion_count.get("Party", 0) + 1
        elif "formal" in text:
            occasion_count["Formal"] = occasion_count.get("Formal", 0) + 1
        elif "casual" in text:
            occasion_count["Casual"] = occasion_count.get("Casual", 0) + 1
        else:
            occasion_count["Other"] = occasion_count.get("Other", 0) + 1

    most_used = max(occasion_count, key=occasion_count.get) if occasion_count else "N/A"

    cursor.execute("SELECT message FROM feedback WHERE message IS NOT NULL AND message != '' LIMIT 5")
    feedbacks = cursor.fetchall()

    return render_template(
        "admin_dashboard.html",
        total_users=total_users,
        total_wardrobe=total_wardrobe,
        total_outfits=total_outfits,
        total_feedback=total_feedback,
        most_used=most_used,
        recent_outfits=recent_outfits,
        feedbacks=feedbacks
    )

@app.route('/admin/users')
def admin_users():
    if 'admin' not in session:
        return redirect('/admin/login')

    cursor = db.cursor()
    cursor.execute("SELECT user_id, username FROM users")
    users = cursor.fetchall()

    return render_template("admin_users.html", users=users)

@app.route('/admin/delete_user/<int:user_id>')
def delete_user(user_id):
    if 'admin' not in session:
        return redirect('/admin/login')

    cursor = db.cursor()
    cursor.execute("DELETE FROM users WHERE user_id=%s", (user_id,))
    db.commit()

    return redirect('/admin/users')

@app.route('/admin/occasions', methods=['GET','POST'])
def admin_occasions():
    if 'admin' not in session:
        return redirect('/admin/login')

    cursor = db.cursor()

    if request.method == 'POST':
        occasion = request.form['occasion']
        cursor.execute(
            "INSERT IGNORE INTO occasions (occasion_name) VALUES (%s)",
            (occasion,)
        )
        db.commit()

    cursor.execute("SELECT * FROM occasions")
    occasions = cursor.fetchall()

    return render_template("admin_occasions.html", occasions=occasions)

@app.route('/admin/delete_occasion/<occasion>')
def delete_occasion(occasion):
    if 'admin' not in session:
        return redirect('/admin/login')

    cursor = db.cursor()
    cursor.execute(
        "DELETE FROM occasions WHERE occasion_name=%s",
        (occasion,)
    )
    db.commit()

    return redirect('/admin/occasions')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect('/admin/login')

# ================= FEEDBACK =================
@app.route('/feedback', methods=['POST'])
def feedback():
    msg = request.form.get('message', '').strip()
    if msg:
        cursor = db.cursor()
        cursor.execute("INSERT INTO feedback (message) VALUES (%s)", (msg,))
        db.commit()
        flash("Thank you! Your feedback has been submitted.", "success")
    return redirect('/')

# ================= START =================
if __name__ == "__main__":
    app.run(debug=True)