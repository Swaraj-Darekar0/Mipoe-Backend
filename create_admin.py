from app import app                        # re-uses the configured Flask app
from models import bcrypt, Admin # Only import bcrypt and Admin model
from config import Config
from supabase import create_client, Client

# Initialize Supabase client
supabase: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

# Remove app context as db.session is no longer used
# with app.app_context(): # open application context

admin_username = "MainAdmin"
admin_email = "admin@gmail.com"
admin_password = "swaraj"

# Hash the password
hashed_password = bcrypt.generate_password_hash(admin_password).decode('utf-8')

# Prepare data for Supabase insertion
new_admin = {
    "username": admin_username,
    "email": admin_email,
    "password_hash": hashed_password
}

try:
    # Insert into the 'admin' table
    response = supabase.table('admin').insert([new_admin]).execute()

    if response.data:
        print("Admin user created successfully:", response.data[0]['username'])
    else:
        print("Failed to create admin user:", response.count)
        print("Error details:", response.data)

except Exception as e:
    print(f"Error creating admin user: {e}")