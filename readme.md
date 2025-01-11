# Codenames
Replicates the board game codenames. Their official online website is at https://codenames.game. This project differs by
aiming to improve the board game experience with many people through allowing an easy interface to upload and play
the game on a larger external display which may be in addition to the actual board game.

## Features
- [x] codenames gameplay loop
    - live game updated with multiple players through web sockets
    - with minimal inteface (e.i. not keeping track of turns to minimize buttons pressed)
    - communication / turns is dealt outside of the game 
- [x] various wordbanks to choose from
- [ ] upload picture board to website and have it populate the board
- [ ] option to save hints and guess order
- [ ] game history

## tech stack
- this project is written with [fasthtml](https://github.com/AnswerDotAI/fasthtmlc)
    - a fullstack python framework which leverages [htmx](https://htmx.org/) and [starlette](https://www.starlette.io/)
    - the main concept is using python object as html elements that are then converted by the library
- [SQLalchemy](https://www.sqlalchemy.org/) is also used as an ORM connected to a [SQLite](https://www.sqlite.org/) database for persistance

## how to run
- install python <= 3.12
- create a python env (optional)
- download python dependencies
    - `pip install -r requirements.txt`
- load the database 
    - `python manage.py load database` (in app directory)
- add word default word packs to the game
    - `python manage.py load cards` (in app directory)
- run the app
    - `python main.py` (in app directory)
- to add other word packs make a new line separated file like `app/cards/general.txt` and pass it and a tag name as flags to the load cards command
    -`python manage.py load cards --file_path cards/general.txt --tag general-words` (in app directory)

