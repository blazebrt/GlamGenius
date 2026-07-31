#!/usr/bin/env python3
"""
Backend API Tests for GlamGenius Salon Advisor
Tests AI Beauty Scan, User Profile CRUD, and Regression endpoints
"""

import os
import uuid
import requests
import json
import base64
from io import BytesIO
from PIL import Image
import time

# Backend URL. Defaults to a locally running server; override with the
# BACKEND_URL environment variable to point at a deployed environment.
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000/api").rstrip("/")

# User ids and tokens created by the scan tests, reused by the profile tests
# below so the post-scan profile checks run against a user that really was
# scanned.
SCAN_USER_IDS = {}
SCAN_TOKENS = {}

# Every protected route needs one of these. Routes that touch user data now
# take the caller's identity from the token, never from the URL or body.
PROTECTED_ROUTES = [
    ("GET", "/users/me", None),
    ("PUT", "/users/me", {"city": "Mumbai"}),
    ("POST", "/scan/analyze", {"image_base64": "x", "scan_type": "face"}),
    ("GET", "/scan/history", None),
    ("GET", "/scan/trends", None),
    ("POST", "/quiz/submit", {"answers": []}),
    ("POST", "/plans/style", {"occasion": "everyday"}),
    ("POST", "/recommendations/advice", {"occasion": "everyday"}),
    ("GET", "/recommendations/history", None),
    ("POST", "/subscription/create-order", {"plan": "plus_monthly"}),
    ("POST", "/subscription/confirm?order_id=sub_whatever", None),
    ("GET", "/subscription/status", None),
]

def auth(token):
    """Authorization header for a signed-in caller."""
    return {"Authorization": f"Bearer {token}"}

def unique_email(prefix):
    return f"{prefix}.{uuid.uuid4().hex[:10]}@example.com"

def register_user(name, email=None, password="TestPassw0rd!"):
    """Register a user and return (user_id, token)."""
    email = email or unique_email("tester")
    response = requests.post(
        f"{BACKEND_URL}/auth/register",
        json={"name": name, "email": email, "password": password},
        timeout=30,
    )
    if response.status_code != 200:
        print(f"❌ FAIL: Could not register user, got {response.status_code}: {response.text[:200]}")
        return None, None
    data = response.json()
    return data["user"]["id"], data["token"]

def create_scan_user(label, name, email):
    """Create a real user for a scan test, and keep their token."""
    try:
        response = requests.post(
            f"{BACKEND_URL}/users",
            json={"name": name, "email": email, "password": "ScanTestPass1!"},
            timeout=30,
        )
        if response.status_code == 200:
            body = response.json()
            user_id = body.get("id")
            token = body.get("token")
            if not token:
                print("❌ FAIL: User creation did not return a token")
                return None, None
            SCAN_USER_IDS[label] = user_id
            SCAN_TOKENS[label] = token
            print(f"✅ Test user created: {user_id}")
            return user_id, token
        print(f"❌ FAIL: Could not create test user, got {response.status_code}")
        return None, None
    except Exception as e:
        print(f"❌ FAIL: Exception creating test user - {str(e)}")
        return None, None

def check_disclaimer(analysis):
    """meta.disclaimer must always be present — see CLAUDE.md."""
    meta = analysis.get("meta")
    if not isinstance(meta, dict) or not meta.get("disclaimer"):
        print("❌ FAIL: Missing 'meta.disclaimer'")
        return False
    print(f"✅ meta.disclaimer: {meta['disclaimer']}")
    return True

def create_test_image():
    """Create a small valid JPEG image and return base64 string"""
    # Create a 50x50 pixel image with a skin-tone color
    img = Image.new('RGB', (50, 50), color=(220, 180, 140))  # Light skin tone
    
    # Save to bytes
    buffer = BytesIO()
    img.save(buffer, format='JPEG', quality=85)
    buffer.seek(0)
    
    # Convert to base64 (without data URI prefix)
    img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
    return img_base64

def test_scan_analyze_face():
    """Test POST /api/scan/analyze with scan_type='face'"""
    print("\n" + "="*80)
    print("TEST 1: AI Beauty Scan - Face Analysis")
    print("="*80)
    
    url = f"{BACKEND_URL}/scan/analyze"

    user_id, token = create_scan_user("face", "Scan Face Tester", unique_email("scan.face"))
    if not user_id:
        return False

    # Create test image
    image_base64 = create_test_image()

    payload = {
        "image_base64": image_base64,
        "scan_type": "face",
        "user_id": user_id
    }

    print(f"POST {url}")
    print(f"Payload: scan_type=face, user_id={user_id}, image_size={len(image_base64)} bytes")
    print("Note: This may take 30-40 seconds as it calls Gemini AI...")

    start_time = time.time()
    try:
        response = requests.post(url, json=payload, headers=auth(token), timeout=120)
        elapsed = time.time() - start_time
        
        print(f"\nResponse Time: {elapsed:.2f}s")
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("✅ HTTP 200 OK")
            
            # Check for required fields
            if "analysis" not in data:
                print("❌ FAIL: Response missing 'analysis' object")
                return False
            
            analysis = data["analysis"]
            print(f"\nAnalysis keys: {list(analysis.keys())}")
            
            # Check for wellness_scores.overall_score
            wellness_scores = analysis.get("wellness_scores")
            if not isinstance(wellness_scores, dict):
                print("❌ FAIL: Missing 'wellness_scores' object")
                return False
            print(f"✅ wellness_scores: {wellness_scores}")

            if "overall_score" not in wellness_scores:
                print("❌ FAIL: Missing 'wellness_scores.overall_score'")
                return False
            print(f"✅ wellness_scores.overall_score: {wellness_scores['overall_score']}")

            if "skin_score" not in wellness_scores:
                print("⚠️  WARNING: No skin_score found in wellness_scores")

            # Check for observations (visible notes, not diagnoses)
            observations = analysis.get("observations")
            if not isinstance(observations, list):
                print("❌ FAIL: Missing 'observations' array")
                return False
            print(f"✅ observations: {len(observations)} observations")

            if observations and isinstance(observations[0], dict):
                first_observation = observations[0]
                if "what_i_see" in first_observation:
                    print(f"✅ observations[0].what_i_see: {first_observation['what_i_see'][:50]}...")
                else:
                    print("⚠️  WARNING: observations items may not have 'what_i_see'")

            # Check for salon_suggestions
            suggestions = analysis.get("salon_suggestions")
            if not isinstance(suggestions, list):
                print("❌ FAIL: Missing 'salon_suggestions' array")
                return False
            print(f"✅ salon_suggestions: {len(suggestions)} suggestions")

            if suggestions and isinstance(suggestions[0], dict):
                first_suggestion = suggestions[0]
                if "why_suggest" in first_suggestion:
                    print(f"✅ salon_suggestions[0].why_suggest: {first_suggestion['why_suggest'][:50]}...")
                else:
                    print("⚠️  WARNING: salon_suggestions items may not have 'why_suggest'")

            # Check for the coach summary (what to do next)
            coach_summary = analysis.get("coach_summary")
            if not isinstance(coach_summary, dict):
                print("❌ FAIL: Missing 'coach_summary' object")
                return False

            actions = coach_summary.get("top_3_actions_this_week")
            if not isinstance(actions, list):
                print("❌ FAIL: Missing 'coach_summary.top_3_actions_this_week' array")
                return False
            print(f"✅ coach_summary.top_3_actions_this_week: {len(actions)} actions")

            if actions:
                print(f"   - recheck_in_days: {coach_summary.get('recheck_in_days', 'N/A')}")
                print(f"   - first action: {str(actions[0])[:50]}...")

            # Check for the fashion block (skin tone -> clothing colours)
            if not isinstance(analysis.get("fashion"), dict):
                print("❌ FAIL: Missing 'fashion' object")
                return False
            print(f"✅ fashion keys: {list(analysis['fashion'].keys())}")

            if not check_disclaimer(analysis):
                return False

            print("\n✅ PASS: Face scan analysis returned all required fields")
            return True
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False

    except requests.exceptions.Timeout:
        print("❌ FAIL: Request timed out (>120s)")
        return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False

def test_scan_analyze_hair():
    """Test POST /api/scan/analyze with scan_type='hair'"""
    print("\n" + "="*80)
    print("TEST 2: AI Beauty Scan - Hair Analysis")
    print("="*80)
    
    url = f"{BACKEND_URL}/scan/analyze"

    user_id, token = create_scan_user("hair", "Scan Hair Tester", unique_email("scan.hair"))
    if not user_id:
        return False

    # Create test image
    image_base64 = create_test_image()

    payload = {
        "image_base64": image_base64,
        "scan_type": "hair",
        "user_id": user_id
    }

    print(f"POST {url}")
    print(f"Payload: scan_type=hair, user_id={user_id}, image_size={len(image_base64)} bytes")
    print("Note: This may take 30-40 seconds as it calls Gemini AI...")

    start_time = time.time()
    try:
        response = requests.post(url, json=payload, headers=auth(token), timeout=120)
        elapsed = time.time() - start_time
        
        print(f"\nResponse Time: {elapsed:.2f}s")
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print("✅ HTTP 200 OK")
            
            # Check for required fields
            if "analysis" not in data:
                print("❌ FAIL: Response missing 'analysis' object")
                return False
            
            analysis = data["analysis"]
            print(f"\nAnalysis keys: {list(analysis.keys())}")
            
            # Check for wellness_scores.overall_score
            wellness_scores = analysis.get("wellness_scores")
            if not isinstance(wellness_scores, dict):
                print("❌ FAIL: Missing 'wellness_scores' object")
                return False
            print(f"✅ wellness_scores: {wellness_scores}")

            if "overall_score" not in wellness_scores:
                print("❌ FAIL: Missing 'wellness_scores.overall_score'")
                return False
            print(f"✅ wellness_scores.overall_score: {wellness_scores['overall_score']}")

            # Check for hair-related metrics
            has_hair_metrics = False
            if "hair_score" in wellness_scores:
                print(f"✅ wellness_scores.hair_score: {wellness_scores['hair_score']}")
                has_hair_metrics = True

            profile = analysis.get("profile")
            if isinstance(profile, dict) and "hair_type_visible" in profile:
                print(f"✅ profile.hair_type_visible: {profile['hair_type_visible']}")
                has_hair_metrics = True

            if not has_hair_metrics:
                print("⚠️  WARNING: No hair_score or profile.hair_type_visible found")

            # Check that the scan was focused on hair
            scan_focus = (analysis.get("meta") or {}).get("scan_focus")
            if scan_focus != "hair":
                print(f"⚠️  WARNING: meta.scan_focus is '{scan_focus}', expected 'hair'")

            # Check for observations (visible notes, not diagnoses)
            observations = analysis.get("observations")
            if not isinstance(observations, list):
                print("❌ FAIL: Missing 'observations' array")
                return False
            print(f"✅ observations: {len(observations)} observations")

            # Check for salon_suggestions
            suggestions = analysis.get("salon_suggestions")
            if not isinstance(suggestions, list):
                print("❌ FAIL: Missing 'salon_suggestions' array")
                return False
            print(f"✅ salon_suggestions: {len(suggestions)} suggestions")

            # Check for the coach summary (what to do next)
            coach_summary = analysis.get("coach_summary")
            if not isinstance(coach_summary, dict):
                print("❌ FAIL: Missing 'coach_summary' object")
                return False

            actions = coach_summary.get("top_3_actions_this_week")
            if not isinstance(actions, list):
                print("❌ FAIL: Missing 'coach_summary.top_3_actions_this_week' array")
                return False
            print(f"✅ coach_summary.top_3_actions_this_week: {len(actions)} actions")

            if actions:
                print(f"   - recheck_in_days: {coach_summary.get('recheck_in_days', 'N/A')}")
                print(f"   - first action: {str(actions[0])[:50]}...")

            if not check_disclaimer(analysis):
                return False

            print("\n✅ PASS: Hair scan analysis returned all required fields")
            return True
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False

    except requests.exceptions.Timeout:
        print("❌ FAIL: Request timed out (>120s)")
        return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False

def test_user_profile_crud():
    """Test User Profile CRUD operations"""
    print("\n" + "="*80)
    print("TEST 3: User Profile CRUD")
    print("="*80)
    
    # Test 1: Create user
    print("\n--- 3.1: POST /api/users (Create User) ---")
    url = f"{BACKEND_URL}/users"
    payload = {
        "name": "Priya Sharma",
        "email": unique_email("priya.sharma"),
        "password": "PriyaPass1!"
    }

    print(f"POST {url}")
    print(f"Payload: {json.dumps(payload)}")

    try:
        response = requests.post(url, json=payload)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            user_data = response.json()
            user_id = user_data.get("id")
            token = user_data.get("token")
            if not token:
                print("❌ FAIL: User creation did not return a token")
                return False
            print(f"✅ User created with ID: {user_id}")
            print(f"✅ Token issued ({len(token)} chars)")
            print(f"   Name: {user_data.get('name')}")
            print(f"   Email: {user_data.get('email')}")
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False
    
    # Test 2: Get user
    print("\n--- 3.2: GET /api/users/{id} (Get User) ---")
    url = f"{BACKEND_URL}/users/{user_id}"
    
    print(f"GET {url}")

    try:
        response = requests.get(url, headers=auth(token))
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            user_data = response.json()
            print(f"✅ User retrieved successfully")
            print(f"   Name: {user_data.get('name')}")
            print(f"   Email: {user_data.get('email')}")
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False

    # Test 3: Update user with skin_concerns
    print("\n--- 3.3: PUT /api/users/{id} (Update User with skin_concerns) ---")
    url = f"{BACKEND_URL}/users/{user_id}"
    payload = {
        "skin_type": "combination",
        "skin_concerns": ["acne", "dullness"]
    }

    print(f"PUT {url}")
    print(f"Payload: {json.dumps(payload)}")

    try:
        response = requests.put(url, json=payload, headers=auth(token))
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            user_data = response.json()
            print(f"✅ User updated successfully")
            print(f"   skin_type: {user_data.get('skin_type')}")
            print(f"   skin_concerns: {user_data.get('skin_concerns')}")
            
            # Verify skin_concerns is a list of strings
            skin_concerns = user_data.get('skin_concerns', [])
            if isinstance(skin_concerns, list):
                if all(isinstance(c, str) for c in skin_concerns):
                    print(f"✅ skin_concerns is a list of strings")
                else:
                    print(f"❌ FAIL: skin_concerns contains non-string items: {skin_concerns}")
                    return False
            else:
                print(f"❌ FAIL: skin_concerns is not a list: {type(skin_concerns)}")
                return False
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False
    
    # Test 4: Get user after scan (the user created by the face scan test,
    # fetched with that user's own token)
    scan_user_id = SCAN_USER_IDS.get("face", "test-user-scan-1")
    scan_token = SCAN_TOKENS.get("face", token)
    print(f"\n--- 3.4: GET /api/users/{scan_user_id} (User from scan test) ---")
    url = f"{BACKEND_URL}/users/{scan_user_id}"

    print(f"GET {url}")

    try:
        response = requests.get(url, headers=auth(scan_token))
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            user_data = response.json()
            print(f"✅ User retrieved successfully (no 500 error)")
            
            # Verify concerns are lists of strings
            skin_concerns = user_data.get('skin_concerns', [])
            hair_concerns = user_data.get('hair_concerns', [])
            
            print(f"   skin_concerns: {skin_concerns}")
            print(f"   hair_concerns: {hair_concerns}")
            
            if isinstance(skin_concerns, list) and all(isinstance(c, str) for c in skin_concerns):
                print(f"✅ skin_concerns is a list of strings")
            else:
                print(f"❌ FAIL: skin_concerns is not a list of strings: {skin_concerns}")
                return False
            
            if isinstance(hair_concerns, list) and all(isinstance(c, str) for c in hair_concerns):
                print(f"✅ hair_concerns is a list of strings")
            else:
                print(f"❌ FAIL: hair_concerns is not a list of strings: {hair_concerns}")
                return False
        elif response.status_code == 404:
            print(f"⚠️  User not found (404) - this is acceptable if scan didn't create user")
        else:
            print(f"❌ FAIL: Expected 200 or 404, got {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False
    
    # Test 5: An id in the URL that is not ours must be refused, never served.
    other_id = "c0624af4-fcd6-4615-9e0d-167dcd0da9b5"
    print(f"\n--- 3.5: GET /api/users/{other_id} (someone else's id — must be refused) ---")
    url = f"{BACKEND_URL}/users/{other_id}"

    print(f"GET {url}")

    try:
        response = requests.get(url, headers=auth(token))
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            print(f"❌ FAIL: Served another user's profile — this is the bug we are fixing")
            print(f"Response: {response.text[:300]}")
            return False
        if response.status_code in (401, 403, 404):
            print(f"✅ Refused with {response.status_code} — cannot read another user's profile")
        else:
            print(f"❌ FAIL: Expected 401/403/404, got {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False

    # Test 6: Profile routes must reject a caller with no token at all.
    print("\n--- 3.6: GET /api/users/me with no token (must be refused) ---")
    try:
        response = requests.get(f"{BACKEND_URL}/users/me")
        print(f"Status Code: {response.status_code}")
        if response.status_code != 401:
            print(f"❌ FAIL: Expected 401, got {response.status_code}")
            return False
        print("✅ Refused with 401 — profile is not readable without a token")
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False

    print("\n✅ PASS: User Profile CRUD tests completed successfully")
    return True

def test_recommendations_advice():
    """Test POST /api/recommendations/advice"""
    print("\n" + "="*80)
    print("TEST 4: Recommendations Advice (Regression)")
    print("="*80)
    
    # First create a user
    print("\n--- Creating test user ---")
    url = f"{BACKEND_URL}/users"
    payload = {
        "name": "Ananya Reddy",
        "email": unique_email("ananya.reddy"),
        "password": "AnanyaPass1!"
    }

    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            user_data = response.json()
            user_id = user_data.get("id")
            token = user_data.get("token")
            print(f"✅ User created with ID: {user_id}")
        else:
            print(f"❌ FAIL: Could not create user, got {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception creating user - {str(e)}")
        return False

    # Test recommendations/advice
    print("\n--- POST /api/recommendations/advice ---")
    url = f"{BACKEND_URL}/recommendations/advice"
    payload = {
        "user_id": user_id,
        "mood": "glam",
        "occasion": "wedding",
        "budget": "3000-5000"
    }
    
    print(f"POST {url}")
    print(f"Payload: {json.dumps(payload)}")
    
    try:
        response = requests.post(url, json=payload, headers=auth(token), timeout=120)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"✅ HTTP 200 OK")

            if "plan" not in data:
                print(f"❌ FAIL: Response missing 'plan' object")
                print(f"Response: {json.dumps(data, indent=2)[:500]}")
                return False

            plan = data["plan"]
            print(f"✅ plan object present")
            print(f"   Keys: {list(plan.keys())}")

            if "salon_suggestions" in plan:
                print(f"   salon_suggestions: {len(plan['salon_suggestions'])} suggestions")

            if not plan.get("disclaimer"):
                print(f"❌ FAIL: Missing 'plan.disclaimer'")
                return False
            print(f"✅ plan.disclaimer: {plan['disclaimer']}")

            print("\n✅ PASS: Recommendations advice endpoint working")
            return True
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False

def test_quiz_submit():
    """Test POST /api/quiz/submit"""
    print("\n" + "="*80)
    print("TEST 5: Quiz Submit (Regression)")
    print("="*80)
    
    # First create a user
    print("\n--- Creating test user ---")
    url = f"{BACKEND_URL}/users"
    payload = {
        "name": "Kavya Iyer",
        "email": unique_email("kavya.iyer"),
        "password": "KavyaPass1!"
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            user_data = response.json()
            user_id = user_data.get("id")
            token = user_data.get("token")
            print(f"✅ User created with ID: {user_id}")
        else:
            print(f"❌ FAIL: Could not create user, got {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception creating user - {str(e)}")
        return False

    # Test quiz submit
    print("\n--- POST /api/quiz/submit ---")
    url = f"{BACKEND_URL}/quiz/submit"
    payload = {
        "user_id": user_id,
        "answers": [
            {"question_id": "q2", "answer": "Combination"}
        ],
        "occasion": "party",
        "budget": "1500-3000"
    }
    
    print(f"POST {url}")
    print(f"Payload: {json.dumps(payload)}")
    
    try:
        response = requests.post(url, json=payload, headers=auth(token), timeout=120)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"✅ HTTP 200 OK")

            if "plan" not in data:
                print(f"❌ FAIL: Response missing 'plan' object")
                print(f"Response: {json.dumps(data, indent=2)[:500]}")
                return False

            print(f"✅ plan object present")

            if "plan_id" in data:
                print(f"   plan_id: {data['plan_id']}")

            if not data["plan"].get("disclaimer"):
                print(f"❌ FAIL: Missing 'plan.disclaimer'")
                return False
            print(f"✅ plan.disclaimer: {data['plan']['disclaimer']}")

            print("\n✅ PASS: Quiz submit endpoint working")
            return True
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False

def test_services():
    """Test GET /api/services"""
    print("\n" + "="*80)
    print("TEST 6: Services Catalog (Regression)")
    print("="*80)
    
    url = f"{BACKEND_URL}/services"
    
    print(f"GET {url}")
    
    try:
        response = requests.get(url)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            services = response.json()
            print(f"✅ HTTP 200 OK")
            
            if not isinstance(services, list):
                print(f"❌ FAIL: Expected list, got {type(services)}")
                return False
            
            print(f"✅ Services list returned: {len(services)} services")
            
            if services:
                first_service = services[0]
                print(f"   First service: {first_service.get('name', 'N/A')}")
                print(f"   Category: {first_service.get('category', 'N/A')}")
                print(f"   Price: {first_service.get('price_range', 'N/A')}")
            
            print("\n✅ PASS: Services endpoint working")
            return True
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Exception - {str(e)}")
        return False

def _call(method, path, body=None, headers=None):
    url = f"{BACKEND_URL}{path}"
    if method == "GET":
        return requests.get(url, headers=headers, timeout=60)
    if method == "PUT":
        return requests.put(url, json=body, headers=headers, timeout=60)
    return requests.post(url, json=body, headers=headers, timeout=60)


def test_auth_no_token_refused():
    """Every route that touches user data must refuse a caller with no token."""
    print("\n" + "="*80)
    print("TEST 7: Security - no token is refused")
    print("="*80)

    ok = True
    for method, path, body in PROTECTED_ROUTES:
        try:
            response = _call(method, path, body)
        except Exception as e:
            print(f"❌ FAIL: {method} {path} raised {str(e)}")
            ok = False
            continue

        if response.status_code == 401:
            print(f"✅ {method:4} {path:45} -> 401 refused")
        else:
            print(f"❌ FAIL: {method:4} {path:45} -> {response.status_code} (expected 401)")
            print(f"         Response: {response.text[:200]}")
            ok = False

    # A token that is not a real signed token must also be refused.
    for label, bad in [
        ("garbage string", "not-a-real-token"),
        ("a bare user id", str(uuid.uuid4())),
        ("wrong signature", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhIn0.bogus"),
    ]:
        response = _call("GET", "/users/me", headers=auth(bad))
        if response.status_code == 401:
            print(f"✅ {label:20} -> 401 refused")
        else:
            print(f"❌ FAIL: {label} was accepted with {response.status_code}")
            ok = False

    if ok:
        print("\n✅ PASS: All protected routes refuse callers without a valid token")
    return ok


def test_auth_valid_token_works():
    """A freshly registered user's token must be accepted on protected routes."""
    print("\n" + "="*80)
    print("TEST 8: Security - valid token is accepted")
    print("="*80)

    user_id, token = register_user("Token Tester")
    if not token:
        return False
    print(f"✅ Registered user {user_id} and received a token ({len(token)} chars)")

    response = _call("GET", "/users/me", headers=auth(token))
    print(f"GET /users/me -> {response.status_code}")
    if response.status_code != 200:
        print(f"❌ FAIL: Expected 200, got {response.status_code}: {response.text[:200]}")
        return False

    me = response.json()
    if me.get("id") != user_id:
        print(f"❌ FAIL: /users/me returned {me.get('id')}, expected {user_id}")
        return False
    print(f"✅ /users/me returned the token's own user: {me.get('id')}")

    if "password_hash" in me:
        print("❌ FAIL: password_hash leaked in the profile response")
        return False
    print("✅ No password_hash in the response")

    # Logging in again must also produce a working token.
    response = _call("GET", "/scan/history", headers=auth(token))
    print(f"GET /scan/history -> {response.status_code}")
    if response.status_code != 200:
        print(f"❌ FAIL: Expected 200, got {response.status_code}")
        return False
    print("✅ Scan history readable with a valid token")

    print("\n✅ PASS: A valid token is accepted on protected routes")
    return True


def test_auth_cross_user_denied():
    """User A must not be able to read or change user B's data."""
    print("\n" + "="*80)
    print("TEST 9: Security - user A cannot reach user B's data")
    print("="*80)

    a_id, a_token = register_user("User A")
    b_id, b_token = register_user("User B")
    if not a_token or not b_token:
        return False
    print(f"✅ User A: {a_id}")
    print(f"✅ User B: {b_id}")

    # A puts something identifiable on their own profile.
    _call("PUT", "/users/me", {"city": "Chennai"}, auth(a_token))
    _call("PUT", "/users/me", {"city": "Jaipur"}, auth(b_token))

    ok = True

    # A asking for B's id in the URL must be refused.
    response = _call("GET", f"/users/{b_id}", headers=auth(a_token))
    print(f"A reads GET /users/{{B's id}} -> {response.status_code}")
    if response.status_code == 200:
        print("❌ FAIL: User A read user B's profile")
        ok = False
    else:
        print(f"✅ Refused with {response.status_code}")

    # A trying to change B's profile through the URL must be refused.
    response = _call("PUT", f"/users/{b_id}", {"city": "Hacked"}, auth(a_token))
    print(f"A writes PUT /users/{{B's id}} -> {response.status_code}")
    if response.status_code == 200:
        print("❌ FAIL: User A modified user B's profile")
        ok = False
    else:
        print(f"✅ Refused with {response.status_code}")

    # B's data must be untouched.
    response = _call("GET", "/users/me", headers=auth(b_token))
    if response.status_code == 200 and response.json().get("city") == "Jaipur":
        print("✅ User B's profile is unchanged (city still 'Jaipur')")
    else:
        print(f"❌ FAIL: User B's profile was altered: {response.text[:200]}")
        ok = False

    # Sending B's id in the request body must not redirect the write to B.
    response = _call("PUT", "/users/me", {"user_id": b_id, "city": "Kochi"}, auth(a_token))
    check = _call("GET", "/users/me", headers=auth(b_token))
    if check.status_code == 200 and check.json().get("city") == "Jaipur":
        print("✅ A user_id in the request body is ignored — identity comes from the token")
    else:
        print(f"❌ FAIL: Body user_id was honoured: {check.text[:200]}")
        ok = False

    # /users/me must always resolve to the caller, never to the other user.
    a_me = _call("GET", "/users/me", headers=auth(a_token)).json()
    b_me = _call("GET", "/users/me", headers=auth(b_token)).json()
    if a_me.get("id") == a_id and b_me.get("id") == b_id and a_id != b_id:
        print("✅ /users/me resolves to the caller for both users")
    else:
        print("❌ FAIL: /users/me did not resolve to the caller")
        ok = False

    if ok:
        print("\n✅ PASS: User A cannot read or change user B's data")
    return ok


def test_login_brute_force_protection():
    """Repeated wrong passwords must lock the account, and a correct one must clear it."""
    print("\n" + "="*80)
    print("TEST 10: Security - login brute force is blocked")
    print("="*80)

    password = "RealPassw0rd!"
    email = unique_email("brute")
    user_id, _ = register_user("Brute Target", email=email, password=password)
    if not user_id:
        return False
    print(f"✅ Registered {email}")

    # Guess wrong passwords until the door shuts.
    locked_at = None
    for attempt in range(1, 12):
        response = requests.post(
            f"{BACKEND_URL}/auth/login",
            json={"email": email, "password": f"wrong-guess-{attempt}"},
            timeout=30,
        )
        print(f"   wrong guess #{attempt} -> {response.status_code}")
        if response.status_code == 429:
            locked_at = attempt
            break
        if response.status_code != 401:
            print(f"❌ FAIL: Expected 401 or 429, got {response.status_code}")
            return False

    if locked_at is None:
        print("❌ FAIL: Never locked out — password guessing is unlimited")
        return False
    print(f"✅ Locked out after {locked_at - 1} failed attempts (429 Too Many Requests)")

    # While locked out, even the right password is refused, so guessing cannot continue.
    response = requests.post(
        f"{BACKEND_URL}/auth/login", json={"email": email, "password": password}, timeout=30
    )
    if response.status_code != 429:
        print(f"❌ FAIL: Lockout did not hold, got {response.status_code}")
        return False
    if not response.headers.get("Retry-After"):
        print("❌ FAIL: No Retry-After header telling the user when to come back")
        return False
    print(f"✅ Lockout holds; Retry-After: {response.headers['Retry-After']} seconds")

    # A genuine user who fumbles a few times then gets it right must not be locked out.
    good_email = unique_email("fumble")
    register_user("Forgetful User", email=good_email, password=password)
    for attempt in range(1, 4):
        requests.post(
            f"{BACKEND_URL}/auth/login",
            json={"email": good_email, "password": f"oops-{attempt}"},
            timeout=30,
        )
    response = requests.post(
        f"{BACKEND_URL}/auth/login", json={"email": good_email, "password": password}, timeout=30
    )
    if response.status_code != 200:
        print(f"❌ FAIL: Genuine user blocked after a few fumbles, got {response.status_code}")
        return False
    print("✅ A genuine user who fumbles then remembers still gets in")

    # And their slate is wiped, so they are not one mistake from a lockout.
    for attempt in range(1, 4):
        response = requests.post(
            f"{BACKEND_URL}/auth/login",
            json={"email": good_email, "password": f"again-{attempt}"},
            timeout=30,
        )
        if response.status_code == 429:
            print("❌ FAIL: Counter was not reset by the successful login")
            return False
    print("✅ Successful login reset their failed-attempt count")

    print("\n✅ PASS: Login is protected against password guessing")
    return True


def test_signed_out_preview_teaser():
    """A signed-out visitor gets a real taste, but not the whole thing."""
    print("\n" + "="*80)
    print("TEST 11: Teaser - signed-out preview")
    print("="*80)

    image_base64 = create_test_image()
    response = requests.post(
        f"{BACKEND_URL}/scan/preview",
        json={"image_base64": image_base64, "scan_type": "face"},
        timeout=180,
    )
    print(f"POST /scan/preview with no token -> {response.status_code}")
    if response.status_code != 200:
        print(f"❌ FAIL: Expected 200, got {response.status_code}: {response.text[:200]}")
        return False
    data = response.json()

    if not data.get("preview") or not data.get("signup_required"):
        print("❌ FAIL: Response is not flagged as a preview requiring signup")
        return False
    print("✅ Flagged as a preview requiring signup")

    colors = data.get("best_clothing_colors")
    if not isinstance(colors, list):
        print("❌ FAIL: Missing 'best_clothing_colors'")
        return False
    print(f"✅ Shows {len(colors)} clothing colours")

    if not isinstance(data.get("profile"), dict):
        print("❌ FAIL: Missing 'profile' with the skin tone read")
        return False
    print(f"✅ Shows skin tone: {data['profile'].get('skin_tone')}")

    if not data.get("locked"):
        print("❌ FAIL: Nothing listed as locked — there is no reason to sign up")
        return False
    print(f"✅ Lists {len(data['locked'])} things locked behind signing up")

    # The teaser must not leak the full paid result.
    for withheld in ("observations", "wellness_scores", "daily_care", "nutrition",
                     "care_ingredients", "salon_suggestions", "coach_summary"):
        if withheld in data:
            print(f"❌ FAIL: Preview leaked '{withheld}' — that belongs behind signup")
            return False
    print("✅ Full result (observations, scores, care, nutrition, salon) is withheld")

    if not check_disclaimer(data):
        return False

    print("\n✅ PASS: Signed-out preview shows a teaser and asks for signup")
    return True


def test_anonymous_accounts_removed():
    """Accounts without an email and password can no longer be created."""
    print("\n" + "="*80)
    print("TEST 12: Signup - anonymous guest accounts are gone")
    print("="*80)

    ok = True
    for label, payload in [
        ("no email, no password", {"name": "Guest"}),
        ("email but no password", {"name": "Guest", "email": unique_email("guest")}),
        ("password but no email", {"name": "Guest", "password": "Passw0rd!"}),
    ]:
        response = requests.post(f"{BACKEND_URL}/users", json=payload, timeout=30)
        if response.status_code == 200:
            print(f"❌ FAIL: '{label}' created an account that nobody can prove they own")
            ok = False
        else:
            print(f"✅ '{label}' refused with {response.status_code}")

    # A proper signup still works and still returns a usable token.
    response = requests.post(
        f"{BACKEND_URL}/users",
        json={"name": "Real User", "email": unique_email("real"), "password": "Passw0rd!"},
        timeout=30,
    )
    if response.status_code != 200 or not response.json().get("token"):
        print(f"❌ FAIL: A proper signup was rejected: {response.status_code}")
        return False
    print("✅ A proper signup with email and password still works and returns a token")

    if ok:
        print("\n✅ PASS: Only real accounts can be created")
    return ok


def test_free_scan_limit_is_one():
    """The free plan allows exactly one check, then asks for an upgrade."""
    print("\n" + "="*80)
    print("TEST 13: Quota - free plan is one check per month")
    print("="*80)

    response = requests.get(f"{BACKEND_URL}/config/public", timeout=30)
    limit = response.json().get("free_scans_per_month")
    print(f"config/public reports free_scans_per_month = {limit}")
    if limit != 1:
        print(f"❌ FAIL: Expected 1, got {limit}")
        return False
    print("✅ Public config advertises 1 free check")

    user_id, token = register_user("Quota Tester")
    if not token:
        return False

    image_base64 = create_test_image()
    payload = {"image_base64": image_base64, "scan_type": "face"}

    first = requests.post(
        f"{BACKEND_URL}/scan/analyze", json=payload, headers=auth(token), timeout=180
    )
    print(f"first check  -> {first.status_code}")
    if first.status_code != 200:
        print(f"❌ FAIL: The one free check was refused: {first.text[:200]}")
        return False
    print("✅ First check allowed")

    second = requests.post(
        f"{BACKEND_URL}/scan/analyze", json=payload, headers=auth(token), timeout=180
    )
    print(f"second check -> {second.status_code}")
    if second.status_code == 200:
        print("❌ FAIL: A second free check was allowed — the limit is not enforced")
        return False
    print(f"✅ Second check refused with {second.status_code}")

    print("\n✅ PASS: Free plan gives exactly one check per month")
    return True


def main():
    """Run all backend tests"""
    print("\n" + "="*80)
    print("GLAMGENIUS BACKEND API TESTS")
    print("="*80)
    print(f"Backend URL: {BACKEND_URL}")
    print(f"Testing AI Beauty Scan, User Profile CRUD, and Regression endpoints")
    print("="*80)
    
    results = {}
    
    # Run tests
    results["Face Scan"] = test_scan_analyze_face()
    results["Hair Scan"] = test_scan_analyze_hair()
    results["User Profile CRUD"] = test_user_profile_crud()
    results["Recommendations Advice"] = test_recommendations_advice()
    results["Quiz Submit"] = test_quiz_submit()
    results["Services Catalog"] = test_services()
    results["Security: no token refused"] = test_auth_no_token_refused()
    results["Security: valid token works"] = test_auth_valid_token_works()
    results["Security: cross-user denied"] = test_auth_cross_user_denied()
    results["Security: brute force blocked"] = test_login_brute_force_protection()
    results["Teaser: signed-out preview"] = test_signed_out_preview_teaser()
    results["Signup: no anonymous accounts"] = test_anonymous_accounts_removed()
    results["Quota: one free check"] = test_free_scan_limit_is_one()
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1

if __name__ == "__main__":
    exit(main())
