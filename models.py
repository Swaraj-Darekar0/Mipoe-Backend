from datetime import datetime

class Brand:
    def __init__(self, id=None, username=None, email=None, password_hash=None):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash

    def set_password(self, password):
        self.password_hash = password

    def check_password(self, password):
        return self.password_hash == password

class Creator:
    def __init__(self, id=None, username=None, email=None, password_hash=None, profile_completed=False,
                 nickname=None, bio=None, phone=None, join_date=None):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.profile_completed = profile_completed
        self.nickname = nickname
        self.bio = bio
        self.phone = phone
        self.join_date = join_date if join_date else datetime.utcnow().date() # Default to today's date

    def set_password(self, password):
        self.password_hash = password

    def check_password(self, password):
        return self.password_hash == password

class Admin:
    def __init__(self, id=None, username=None, email=None, password_hash=None):
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash

    def set_password(self, password):
        self.password_hash = password

    def check_password(self, password):
        return self.password_hash == password

class Campaign:
    def __init__(self, id=None, brand_id=None, platform=None, budget=None, cpv=None, hashtag=None, 
                 audio=None, deadline=None, name=None, requirements=None, is_active=True, 
                 total_view_count=0, view_threshold=0, category='fashion_clothing'):  
        self.id = id
        self.brand_id = brand_id
        self.platform = platform
        self.budget = budget
        self.cpv = cpv
        self.hashtag = hashtag
        self.audio = audio
        self.deadline = deadline
        self.name = name
        self.requirements = requirements
        self.is_active = is_active
        self.total_view_count = total_view_count
        self.view_threshold = view_threshold
        self.category = category  

class SubmittedClip:
    def __init__(self, id=None, creator_id=None, campaign_id=None, clip_url=None, submitted_at=None, 
                 is_deleted_by_admin=False, feedback=None):
        self.id = id
        self.creator_id = creator_id
        self.campaign_id = campaign_id
        self.clip_url = clip_url
        self.submitted_at = submitted_at if submitted_at else datetime.utcnow() # Matches TIMESTAMP WITHOUT TIME ZONE
        self.is_deleted_by_admin = is_deleted_by_admin
        self.feedback = feedback

class AcceptedClip:
    def __init__(self, id=None, creator_id=None, campaign_id=None, clip_url=None, submitted_at=None,
                 media_id=None, view_count=None, caption=None, instagram_posted_at=None):
        self.id = id
        self.creator_id = creator_id
        self.campaign_id = campaign_id
        self.clip_url = clip_url
        self.submitted_at = submitted_at if submitted_at else datetime.utcnow() # Matches TIMESTAMP WITHOUT TIME ZONE
        self.media_id = media_id
        self.view_count = view_count
        self.caption = caption
        self.instagram_posted_at = instagram_posted_at if instagram_posted_at else datetime.utcnow() # Matches TIMESTAMP WITHOUT TIME ZONE 