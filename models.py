
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class CardLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(100), nullable=False)
    uid_formatted = db.Column(db.String(100))
    uid_decimal = db.Column(db.String(100))
    card_type = db.Column(db.String(100))
    reader_name = db.Column(db.String(200))
    interface = db.Column(db.String(50))
    atr = db.Column(db.String(200))
    block_data = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "uid": self.uid,
            "uid_formatted": self.uid_formatted,
            "type": self.card_type,
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
