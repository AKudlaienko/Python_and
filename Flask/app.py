#!/usr/bin/env python3

from flask import Flask, render_template, url_for, request, json
from flaskext.mysql import MySQL
from werkzeug import generate_password_hash, check_password_hash

app = Flask(__name__)

# MySQL configurations
mysql = MySQL()
app.config['MYSQL_DATABASE_USER'] = 'flask'
app.config['MYSQL_DATABASE_PASSWORD'] = ''
app.config['MYSQL_DATABASE_DB'] = 'BucketList'
app.config['MYSQL_DATABASE_HOST'] = ''
mysql.init_app(app)
conn = mysql.connect()
cursor = conn.cursor()


@app.route('/')
# def index():
#     return 'Index page'
def main():
    # bootstrap_css_url = url_for('static', filename='css/bootstrap.min.css')
    # print(bootstrap_css_url)
    # jumbotron_narrow_url = url_for('static', filename='css/jumbotron_narrow.css')
    # print(jumbotron_narrow_url)
    return render_template('index.html')


@app.route('/showSignUp')
def showSignUp():
    return render_template('signup.html')

@app.route('/signUp', methods=['POST'])
def signUp():
    # create user
    # read the posted values from the UI
    _name = request.form['inputName']
    _email = request.form['inputEmail']
    _password = request.form['inputPassword']


    # validate the received values
    if _name and _email and _password:
        _hashed_password = generate_password_hash(_password)
        cursor.callproc('sp_createUser', (_name, _email, _hashed_password))
        data = cursor.fetchall()

        if len(data) is 0:
            conn.commit()
            return json.dumps({'message': 'User created successfully !'})
        else:
            return json.dumps({'error': str(data[0])})
        #return json.dumps({'html': '<span>All fields good !!</span>'})
    else:
        return json.dumps({'html': '<span>Enter the required fields</span>'})











    cursor.callproc('sp_createUser',(_name,_email,_hashed_password))


if __name__ == "__main__":
    app.run()
