# web.py - 极简后台
from flask import Flask, render_template, request, session, redirect, url_for
from database import get_conn
from config import WEB_PASSWORD, ADMIN_IDS
import hashlib

app = Flask(__name__)
app.secret_key = "secret_2025"

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form['password']
        telegram_id = int(request.form.get('telegram_id', 0))
        if hashlib.md5(password.encode()).hexdigest() == hashlib.md5(WEB_PASSWORD.encode()).hexdigest() and telegram_id in ADMIN_IDS:
            session['admin'] = True
            session['user_id'] = telegram_id
            return redirect('/dashboard')
    return '''
    <form method="post">
        <h2>管理员登录</h2>
        密码: <input type="password" name="password"><br><br>
        Telegram ID: <input type="number" name="telegram_id"><br><br>
        <input type="submit" value="登录">
    </form>
    '''

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin'):
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

@app.route('/dashboard')
@admin_required
def dashboard():
    return "<h1>欢迎来到后台！这里可以添加更多页面</h1><p>所有配置通过数据库操作，机器人自动读取</p>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
