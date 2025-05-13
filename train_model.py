import sqlite3
import pandas as pd
from transformers import DistilBertTokenizer, DistilBertForSequenceClassification, Trainer, TrainingArguments
from datasets import Dataset
import onnxruntime as ort
import torch
import logging

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.FileHandler('train.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# استخراج داده‌ها از دیتابیس
def get_training_data():
    with sqlite3.connect('users.db') as conn:
        df = pd.read_sql_query('SELECT message, response FROM conversations WHERE response IS NOT NULL', conn)
    return df

# آماده‌سازی داده‌ها
def prepare_data(df):
    tokenizer = DistilBertTokenizer.from_pretrained('distilbert-base-uncased')
    texts = df['message'].tolist()
    labels = df['response'].tolist()
    encodings = tokenizer(texts, truncation=True, padding=True, max_length=128)
    dataset = Dataset.from_dict({
        'input_ids': encodings['input_ids'],
        'attention_mask': encodings['attention_mask'],
        'labels': labels
    })
    return dataset

# آموزش مدل
def train_model(dataset):
    model = DistilBertForSequenceClassification.from_pretrained('distilbert-base-uncased', num_labels=len(set(dataset['labels'])))
    training_args = TrainingArguments(
        output_dir='./results',
        num_train_epochs=3,
        per_device_train_batch_size=8,
        warmup_steps=500,
        weight_decay=0.01,
        logging_dir='./logs',
        logging_steps=10,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset
    )
    trainer.train()
    model.save_pretrained('./model')
    tokenizer = DistilBertTokenizer.from_pretrained('distilbert-base-uncased')
    tokenizer.save_pretrained('./model')
    return model

# تبدیل به ONNX
def convert_to_onnx():
    model = DistilBertForSequenceClassification.from_pretrained('./model')
    tokenizer = DistilBertTokenizer.from_pretrained('./model')
    dummy_input = tokenizer("سلام", return_tensors="pt")
    torch.onnx.export(
        model,
        (dummy_input['input_ids'], dummy_input['attention_mask']),
        "model.onnx",
        opset_version=11,
        input_names=['input_ids', 'attention_mask'],
        output_names=['output']
    )
    logger.info("مدل به ONNX تبدیل شد.")

# اصلی
if __name__ == '__main__':
    logger.info("شروع آموزش مدل...")
    df = get_training_data()
    if not df.empty:
        dataset = prepare_data(df)
        model = train_model(dataset)
        convert_to_onnx()
        logger.info("آموزش و تبدیل مدل کامل شد.")
    else:
        logger.error("داده‌ای برای آموزش یافت نشد!")