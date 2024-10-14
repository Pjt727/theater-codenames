from models.game import *
from models.config import Base, engine, session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import os

# needs to import everything from all the models to ensure that the all
#    the tables are created
import argparse
import logging

logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)


def simple_create_database():
    Base.metadata.create_all(engine)


def create_words(file_path: str, tag_name: str):
    add_tag = sqlite_insert(Tag).values({"name": tag_name}).on_conflict_do_nothing([Tag.name])
    session.execute(add_tag)
    # need to get the id of the tag (kind of messy)
    session.commit()
    tag_id = session.scalar(select(Tag.id).filter(Tag.name == tag_name))
    assert tag_id is not None
    with open(file_path, "r") as f:
        phrases = {p.strip() for p in f.readlines()}
        cards = [{"phrase": phrase} for phrase in phrases]
        add_cards = sqlite_insert(Card).values(cards).on_conflict_do_nothing([Card.phrase])
        session.execute(add_cards)
        groupers = [{"card_phrase": phrase, "tag_id": tag_id} for phrase in phrases]
        add_groupers = (
            sqlite_insert(TagCardGrouper)
            .values(groupers)
            .on_conflict_do_nothing([TagCardGrouper.card_phrase, TagCardGrouper.tag_id])
        )
        session.execute(add_groupers)
    session.commit()


BASE_CARDS_DIR = "cards"


# each file in the cards folder represents a group of words
#   under a single tag/ category e.i. computer science
# words may be parts of many different categorys
def create_default_words():
    # related words for computer science found from the relatedwords.io game
    files_tags_to_source_from = [("compsci.txt", "Computer Science"), ("general.txt", "Base Game")]

    for file_name, tag in files_tags_to_source_from:
        create_words(os.path.join(BASE_CARDS_DIR, file_name), tag)


if __name__ == "__main__":
    # i don't think this arg parser is done right
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    load_parser = subparsers.add_parser("load")
    load_parser.add_argument("type", help="Type to load", choices=["cards", "database"])
    load_parser.add_argument("--file_path", help="File path to load cards from")
    load_parser.add_argument("--tag", help="Tag for the loaded cards")
    args = parser.parse_args()
    if args.command == "load":
        if args.type == "cards":
            if args.file_path:
                assert args.tag is not None
                create_words(args.file_path, args.tag)
            else:
                create_default_words()
        elif args.type == "database":
            simple_create_database()
