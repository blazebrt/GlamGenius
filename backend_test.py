#!/usr/bin/env python3
"""
Backend API Tests for GlamGenius Salon Advisor
Tests AI Beauty Scan, User Profile CRUD, and Regression endpoints
"""

import os
import requests
import json
import base64
from io import BytesIO
from PIL import Image
import time

# Backend URL. Defaults to a locally running server; override with the
# BACKEND_URL environment variable to point at a deployed environment.
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000/api").rstrip("/")

# User ids created by the scan tests, reused by the profile tests below so the
# post-scan profile checks run against a user that really was scanned.
SCAN_USER_IDS = {}

def create_scan_user(label, name, email):
    """Create a real user for a scan test. /api/scan/analyze rejects unknown ids."""
    try:
        response = requests.post(
            f"{BACKEND_URL}/users", json={"name": name, "email": email}, timeout=30
        )
        if response.status_code == 200:
            user_id = response.json().get("id")
            SCAN_USER_IDS[label] = user_id
            print(f"✅ Test user created: {user_id}")
            return user_id
        print(f"❌ FAIL: Could not create test user, got {response.status_code}")
        return None
    except Exception as e:
        print(f"❌ FAIL: Exception creating test user - {str(e)}")
        return None

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

    user_id = create_scan_user("face", "Scan Face Tester", "scan.face@example.com")
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
        response = requests.post(url, json=payload, timeout=120)
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

    user_id = create_scan_user("hair", "Scan Hair Tester", "scan.hair@example.com")
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
        response = requests.post(url, json=payload, timeout=120)
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
        "email": "priya.sharma@example.com"
    }
    
    print(f"POST {url}")
    print(f"Payload: {json.dumps(payload)}")
    
    try:
        response = requests.post(url, json=payload)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            user_data = response.json()
            user_id = user_data.get("id")
            print(f"✅ User created with ID: {user_id}")
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
        response = requests.get(url)
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
        response = requests.put(url, json=payload)
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
    
    # Test 4: Get user after scan (the user created by the face scan test)
    scan_user_id = SCAN_USER_IDS.get("face", "test-user-scan-1")
    print(f"\n--- 3.4: GET /api/users/{scan_user_id} (User from scan test) ---")
    url = f"{BACKEND_URL}/users/{scan_user_id}"

    print(f"GET {url}")
    
    try:
        response = requests.get(url)
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
    
    # Test 5: Get previously failing user (c0624af4-fcd6-4615-9e0d-167dcd0da9b5)
    print("\n--- 3.5: GET /api/users/c0624af4-fcd6-4615-9e0d-167dcd0da9b5 (Previously failing user) ---")
    url = f"{BACKEND_URL}/users/c0624af4-fcd6-4615-9e0d-167dcd0da9b5"
    
    print(f"GET {url}")
    
    try:
        response = requests.get(url)
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
            print(f"✅ User not found (404) - this is acceptable if user doesn't exist")
        else:
            print(f"❌ FAIL: Expected 200 or 404, got {response.status_code}")
            print(f"Response: {response.text}")
            return False
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
        "email": "ananya.reddy@example.com"
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            user_data = response.json()
            user_id = user_data.get("id")
            print(f"✅ User created with ID: {user_id}")
        else:
            print(f"⚠️  Could not create user, using test ID")
            user_id = "test-user-recommendations"
    except Exception as e:
        print(f"⚠️  Exception creating user: {str(e)}, using test ID")
        user_id = "test-user-recommendations"
    
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
        response = requests.post(url, json=payload, timeout=120)
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
        "email": "kavya.iyer@example.com"
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            user_data = response.json()
            user_id = user_data.get("id")
            print(f"✅ User created with ID: {user_id}")
        else:
            print(f"⚠️  Could not create user, using test ID")
            user_id = "test-user-quiz"
    except Exception as e:
        print(f"⚠️  Exception creating user: {str(e)}, using test ID")
        user_id = "test-user-quiz"
    
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
        response = requests.post(url, json=payload, timeout=120)
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
