from supabase import create_client
from config import Config

# Initialize Supabase client
supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

def check_and_create_tables():
    """
    Check if required tables exist in the database, and create them if they don't.
    This function is called when the application starts.
    """
    # List of required tables and their SQL definitions
    required_tables = {
        'brands': """
        CREATE TABLE IF NOT EXISTS brands (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(100) NOT NULL,
            phone VARCHAR(20),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        'creators': """
        CREATE TABLE IF NOT EXISTS creators (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(100) NOT NULL,
            phone VARCHAR(20),
            nickname VARCHAR(50),
            bio TEXT,
            profile_completed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        'campaigns': """
        CREATE TABLE IF NOT EXISTS campaigns (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            brand_id UUID REFERENCES brands(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            platform VARCHAR(50) NOT NULL,
            budget DECIMAL(10, 2) NOT NULL,
            cpv DECIMAL(10, 2) NOT NULL,
            hashtag VARCHAR(100) NOT NULL,
            audio TEXT,
            deadline TIMESTAMP WITH TIME ZONE NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            total_view_count INTEGER DEFAULT 0,
            requirements TEXT,
            view_threshold INTEGER DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        'submitted_clips': """
        CREATE TABLE IF NOT EXISTS submitted_clips (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            campaign_id UUID REFERENCES campaigns(id) ON DELETE CASCADE,
            creator_id UUID REFERENCES creators(id) ON DELETE CASCADE,
            clip_url TEXT NOT NULL,
            submitted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            is_deleted_by_admin BOOLEAN DEFAULT FALSE,
            feedback TEXT
        )
        """,
        'accepted_clips': """
        CREATE TABLE IF NOT EXISTS accepted_clips (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            submitted_clip_id UUID REFERENCES submitted_clips(id) ON DELETE CASCADE,
            brand_notes TEXT,
            accepted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """,
        'admins': """
        CREATE TABLE IF NOT EXISTS admins (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(100) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
        """
    }

    try:
        # Execute each table creation SQL
        for table_name, create_sql in required_tables.items():
            try:
                supabase.rpc('execute_sql', {'query': create_sql}).execute()
                print(f"Table {table_name} checked/created successfully")
            except Exception as e:
                print(f"Error creating table {table_name}: {str(e)}")
                raise

        print("All tables have been checked/created successfully")
        return True
    except Exception as e:
        print(f"Error in check_and_create_tables: {str(e)}")
        return False