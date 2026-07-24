#!/usr/bin/env python3
"""
Backend API Tests for GlamGenius Salon Advisor
Tests AI Beauty Scan, User Profile CRUD, and Regression endpoints
"""

import requests
import json
import base64
from io import BytesIO
from PIL import Image
import time

# Backend URL from frontend/.env
BACKEND_URL = "https://salon-advisor-beta.preview.emergentagent.com/api"

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
    
    # Create test image
    image_base64 = create_test_image()
    
    payload = {
        "image_base64": image_base64,
        "scan_type": "face",
        "user_id": "test-user-scan-1"
    }
    
    print(f"POST {url}")
    print(f"Payload: scan_type=face, user_id=test-user-scan-1, image_size={len(image_base64)} bytes")
    print("Note: This may take 30-40 seconds as it calls Gemini AI...")
    
    start_time = time.time()
    try:
        response = requests.post(url, json=payload, timeout=60)
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
            
            # Check for overall_score or health_scores.overall_skin_health
            has_overall_score = False
            if "overall_score" in analysis:
                print(f"✅ overall_score: {analysis['overall_score']}")
                has_overall_score = True
            elif "health_scores" in analysis and isinstance(analysis["health_scores"], dict):
                if "overall_skin_health" in analysis["health_scores"]:
                    print(f"✅ health_scores.overall_skin_health: {analysis['health_scores']['overall_skin_health']}")
                    has_overall_score = True
            
            if not has_overall_score:
                print("❌ FAIL: Missing 'overall_score' or 'health_scores.overall_skin_health'")
                return False
            
            # Check for health_scores
            if "health_scores" not in analysis:
                print("❌ FAIL: Missing 'health_scores' object")
                return False
            print(f"✅ health_scores: {analysis['health_scores']}")
            
            # Check for recommended_treatments
            if "recommended_treatments" not in analysis:
                print("❌ FAIL: Missing 'recommended_treatments' array")
                return False
            
            treatments = analysis["recommended_treatments"]
            print(f"✅ recommended_treatments: {len(treatments)} treatments")
            
            # Check if treatments have expected_results
            if treatments and isinstance(treatments, list):
                first_treatment = treatments[0]
                if isinstance(first_treatment, dict) and "expected_results" in first_treatment:
                    print(f"✅ recommended_treatments[0].expected_results: {first_treatment['expected_results'][:50]}...")
                else:
                    print("⚠️  WARNING: recommended_treatments items may not have 'expected_results'")
            
            # Check for expected_outcomes
            if "expected_outcomes" not in analysis:
                print("❌ FAIL: Missing 'expected_outcomes' array")
                return False
            
            outcomes = analysis["expected_outcomes"]
            print(f"✅ expected_outcomes: {len(outcomes)} outcomes")
            
            if outcomes and isinstance(outcomes, list):
                first_outcome = outcomes[0]
                if isinstance(first_outcome, dict):
                    print(f"   - timeframe: {first_outcome.get('timeframe', 'N/A')}")
                    print(f"   - improvement: {first_outcome.get('improvement', 'N/A')[:50]}...")
            
            print("\n✅ PASS: Face scan analysis returned all required fields")
            return True
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ FAIL: Request timed out (>60s)")
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
    
    # Create test image
    image_base64 = create_test_image()
    
    payload = {
        "image_base64": image_base64,
        "scan_type": "hair",
        "user_id": "test-user-scan-2"
    }
    
    print(f"POST {url}")
    print(f"Payload: scan_type=hair, user_id=test-user-scan-2, image_size={len(image_base64)} bytes")
    print("Note: This may take 30-40 seconds as it calls Gemini AI...")
    
    start_time = time.time()
    try:
        response = requests.post(url, json=payload, timeout=60)
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
            
            # Check for overall_score or health_scores.overall_hair_health
            has_overall_score = False
            if "overall_score" in analysis:
                print(f"✅ overall_score: {analysis['overall_score']}")
                has_overall_score = True
            elif "health_scores" in analysis and isinstance(analysis["health_scores"], dict):
                if "overall_hair_health" in analysis["health_scores"]:
                    print(f"✅ health_scores.overall_hair_health: {analysis['health_scores']['overall_hair_health']}")
                    has_overall_score = True
            
            if not has_overall_score:
                print("❌ FAIL: Missing 'overall_score' or 'health_scores.overall_hair_health'")
                return False
            
            # Check for health_scores with hair/scalp metrics
            if "health_scores" not in analysis:
                print("❌ FAIL: Missing 'health_scores' object")
                return False
            
            health_scores = analysis["health_scores"]
            print(f"✅ health_scores: {health_scores}")
            
            # Check for scalp-related metrics
            has_scalp_metrics = False
            if "scalp_health" in health_scores:
                print(f"✅ health_scores.scalp_health: {health_scores['scalp_health']}")
                has_scalp_metrics = True
            
            if "scalp_condition" in analysis:
                print(f"✅ scalp_condition: {analysis['scalp_condition']}")
                has_scalp_metrics = True
            
            if not has_scalp_metrics:
                print("⚠️  WARNING: No scalp_health or scalp_condition found")
            
            # Check for expected_outcomes
            if "expected_outcomes" not in analysis:
                print("❌ FAIL: Missing 'expected_outcomes' array")
                return False
            
            outcomes = analysis["expected_outcomes"]
            print(f"✅ expected_outcomes: {len(outcomes)} outcomes")
            
            if outcomes and isinstance(outcomes, list):
                first_outcome = outcomes[0]
                if isinstance(first_outcome, dict):
                    print(f"   - timeframe: {first_outcome.get('timeframe', 'N/A')}")
                    print(f"   - improvement: {first_outcome.get('improvement', 'N/A')[:50]}...")
            
            print("\n✅ PASS: Hair scan analysis returned all required fields")
            return True
        else:
            print(f"❌ FAIL: Expected 200, got {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ FAIL: Request timed out (>60s)")
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
    
    # Test 4: Get user after scan (test-user-scan-1)
    print("\n--- 3.4: GET /api/users/test-user-scan-1 (User from scan test) ---")
    url = f"{BACKEND_URL}/users/test-user-scan-1"
    
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
        response = requests.post(url, json=payload, timeout=30)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ HTTP 200 OK")
            
            if "recommendations" not in data:
                print(f"❌ FAIL: Response missing 'recommendations' object")
                print(f"Response: {json.dumps(data, indent=2)[:500]}")
                return False
            
            recommendations = data["recommendations"]
            print(f"✅ recommendations object present")
            print(f"   Keys: {list(recommendations.keys())}")
            
            if "services" in recommendations:
                print(f"   services: {len(recommendations['services'])} services")
            
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
        response = requests.post(url, json=payload, timeout=30)
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ HTTP 200 OK")
            
            if "recommendations" not in data:
                print(f"❌ FAIL: Response missing 'recommendations' object")
                print(f"Response: {json.dumps(data, indent=2)[:500]}")
                return False
            
            print(f"✅ recommendations object present")
            
            if "recommendation_id" in data:
                print(f"   recommendation_id: {data['recommendation_id']}")
            
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
