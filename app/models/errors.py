class NotEnoughCards(Exception):
    def __init__(self, message, needed_cards: int, cards_left: int):
        super().__init__(message)
        self.needed_cards = needed_cards
        self.cards_left = cards_left
