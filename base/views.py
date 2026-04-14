from groq import Groq
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.conf import settings
from xhtml2pdf import pisa
from .models import TripItinerary
import os, re, json

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def home(request):
    return render(request, "home.html")


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == "POST":
        username   = request.POST.get("username", "").strip()
        email      = request.POST.get("email", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        last_name  = request.POST.get("last_name", "").strip()
        password1  = request.POST.get("password1", "")
        password2  = request.POST.get("password2", "")
        if not username or not email or not password1:
            messages.error(request, "Please fill in all required fields.")
            return render(request, "register.html")
        if password1 != password2:
            messages.error(request, "Passwords do not match.")
            return render(request, "register.html")
        if len(password1) < 8:
            messages.error(request, "Password must be at least 8 characters.")
            return render(request, "register.html")
        if User.objects.filter(username=username).exists():
            messages.error(request, "That username is already taken.")
            return render(request, "register.html")
        if User.objects.filter(email=email).exists():
            messages.error(request, "An account with that email already exists.")
            return render(request, "register.html")
        user = User.objects.create_user(
            username=username, email=email,
            password=password1, first_name=first_name, last_name=last_name
        )
        login(request, user)

        # ── After registration: if pending trip in session, generate it now
        if 'pending_trip' in request.session:
            return redirect('generate_pending_trip')

        messages.success(request, f"Welcome aboard, {first_name or username}! ✦")
        return redirect('dashboard')
    return render(request, "register.html")


def login_view(request):
    if request.user.is_authenticated:
        # If there's a pending trip, generate it now
        if 'pending_trip' in request.session:
            return redirect('generate_pending_trip')
        return redirect('dashboard')
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            # If there's a pending trip, generate it now
            if 'pending_trip' in request.session:
                return redirect('generate_pending_trip')
            return redirect(request.GET.get('next', 'dashboard'))
        else:
            messages.error(request, "Invalid username or password.")
    return render(request, "login.html")


def logout_view(request):
    if request.method == "POST":
        logout(request)
        return redirect('login')
    return redirect('home')


def forgot_password_view(request):
    if request.method == "POST":
        email = request.POST.get("email", "").strip()
        try:
            user = User.objects.get(email=email)
            token = default_token_generator.make_token(user)
            uid   = urlsafe_base64_encode(force_bytes(user.pk))
            reset_url = f"{request.scheme}://{request.get_host()}/reset-password/{uid}/{token}/"
            send_mail(
                subject="Reset Your AI Travel Planner Password",
                message=f"Hello {user.first_name or user.username},\n\nClick the link below to reset your password:\n{reset_url}\n\nThis link expires in 24 hours.\n\n— AI Travel Planner",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except User.DoesNotExist:
            pass
        messages.success(request, "If that email is registered, a reset link has been sent.")
        return render(request, "forgot_password.html")
    return render(request, "forgot_password.html")


def reset_password_view(request, uidb64, token):
    try:
        uid  = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user is None or not default_token_generator.check_token(user, token):
        messages.error(request, "This reset link is invalid or has expired.")
        return redirect('forgot_password')
    if request.method == "POST":
        password1 = request.POST.get("password1", "")
        password2 = request.POST.get("password2", "")
        if password1 != password2:
            messages.error(request, "Passwords do not match.")
        elif len(password1) < 8:
            messages.error(request, "Password must be at least 8 characters.")
        else:
            user.set_password(password1)
            user.save()
            messages.success(request, "Password updated! Please sign in.")
            return redirect('login')
    return render(request, "reset_password.html", {"uidb64": uidb64, "token": token})


@login_required(login_url='/login/')
def dashboard_view(request):
    trips = TripItinerary.objects.filter(user=request.user).order_by('-created_at')
    total_days = sum(t.days for t in trips)
    unique_destinations = trips.values('destination').distinct().count()
    from django.utils import timezone
    now = timezone.now()
    this_month = trips.filter(created_at__year=now.year, created_at__month=now.month).count()
    return render(request, "dashboard.html", {
        "trips": trips,
        "total_days": total_days,
        "unique_destinations": unique_destinations,
        "this_month": this_month,
    })


@login_required(login_url='/login/')
def view_trip(request, trip_id):
    trip = get_object_or_404(TripItinerary, id=trip_id, user=request.user)
    return render(request, "result.html", {
        "result": trip.result_text,
        "destination": trip.destination,
        "origin": trip.origin,
        "trip": trip,
    })


@login_required(login_url='/login/')
def delete_trip(request, trip_id):
    if request.method == "POST":
        trip = get_object_or_404(TripItinerary, id=trip_id, user=request.user)
        trip.delete()
        messages.success(request, "Trip deleted.")
    return redirect('dashboard')


# ─────────────────────────────────────────
# PLAN TRIP — saves pending to session if not logged in
# ─────────────────────────────────────────
def plan_trip(request):
    if request.method == "POST":
        destination  = request.POST.get("destination", "").strip()
        origin       = request.POST.get("origin", "").strip()
        days         = int(request.POST.get("days", 3))
        budget       = request.POST.get("budget", "0")
        travel_type  = request.POST.get("travel_type", "Solo")
        members      = request.POST.get("members", "1").strip()

        try:
            members_int = max(1, int(members))
        except ValueError:
            members_int = 1

        try:
            total_budget = float(budget)
            per_person   = total_budget / members_int
        except (ValueError, ZeroDivisionError):
            total_budget = 0
            per_person   = 0

        # ── If not logged in: save trip params in session, redirect to login
        if not request.user.is_authenticated:
            request.session['pending_trip'] = {
                'destination': destination,
                'origin': origin,
                'days': days,
                'budget': total_budget,
                'travel_type': travel_type,
                'members': members_int,
            }
            messages.info(request, "Please sign in or create an account to generate and save your itinerary.")
            return redirect('/login/?next=/generate-pending/')

        return _generate_and_render(request, destination, origin, days, total_budget, per_person, travel_type, members_int)

    return render(request, "home.html")


# ─────────────────────────────────────────
# GENERATE PENDING TRIP (after login/register)
# ─────────────────────────────────────────
@login_required(login_url='/login/')
def generate_pending_trip(request):
    pending = request.session.pop('pending_trip', None)
    if not pending:
        return redirect('home')

    destination  = pending['destination']
    origin       = pending.get('origin', '')
    days         = pending['days']
    total_budget = pending['budget']
    travel_type  = pending['travel_type']
    members_int  = pending['members']

    try:
        per_person = total_budget / members_int
    except ZeroDivisionError:
        per_person = 0

    return _generate_and_render(request, destination, origin, days, total_budget, per_person, travel_type, members_int)


# ─────────────────────────────────────────
# SHARED HELPER — builds prompt, calls AI, saves, renders
# ─────────────────────────────────────────
def _generate_and_render(request, destination, origin, days, total_budget, per_person, travel_type, members_int):
    prompt = f"""
You are an expert AI travel planner.

Origin (Departing From): {origin if origin else 'Not specified'}
Destination: {destination}
Trip Duration: {days} days
Number of Travellers: {members_int} {'person' if members_int == 1 else 'people'} ({travel_type})
Total Budget: ₹{int(total_budget)}
Budget Per Person: ₹{int(per_person)}
Travel Type: {travel_type}

Tasks:
1. If origin is provided, include transport options from {origin if origin else 'origin'} to {destination} (flight, train, bus) with estimated cost and time
2. Check if the total budget is sufficient for {members_int} {'person' if members_int == 1 else 'people'} including travel from origin
3. Estimate the average total cost AND per-person cost (including travel from origin if provided)
4. Suggest a detailed day-by-day itinerary suitable for a {travel_type} group of {members_int}
5. Tailor activities to the group size and type (e.g. family-friendly, romantic spots for couples, adventure for friends)
6. Suggest cheaper alternatives if budget is insufficient for the group

Respond strictly in this format:

Destination: {destination}
Origin: {origin if origin else 'Not specified'}
Trip Duration: {days} days
Number of Travellers: {members_int}
Budget Given: ₹{int(total_budget)} (₹{int(per_person)} per person)
Is Budget Sufficient: Yes / No
Estimated Cost: ₹XXXX total (₹XXXX per person)
{f'Travel from {origin}: [Best mode, duration, estimated cost]' if origin else ''}

Day 1: [Title of the day]
[Detailed activities, timings, food suggestions, transport tips for the group of {members_int}]

Day 2: [Title]
[...]

(continue for all {days} days)

Budget Advice:
[Practical money-saving tips for {members_int} travellers in {destination}]
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        result = response.choices[0].message.content
    except Exception as e:
        result = f"Error generating travel plan: {str(e)}"

    budget_sufficient = bool(re.search(r'Is Budget Sufficient[:\s]+yes', result, re.I))
    trip = TripItinerary.objects.create(
        user=request.user,
        destination=destination,
        origin=origin,
        days=days,
        budget=total_budget,
        travel_type=travel_type,
        members=members_int,
        result_text=result,
        is_budget_sufficient=budget_sufficient,
    )

    return render(request, "result.html", {
        "result": result,
        "destination": destination,
        "origin": origin,
        "trip": trip,
    })


def chat_with_ai(request):
    if request.method == "POST":
        user_message = request.POST.get("message")
        itinerary    = request.POST.get("itinerary")
        prompt = f"""
You are a smart AI travel assistant.

User already has this itinerary:
{itinerary}

User request:
{user_message}

- Modify itinerary if asked, keeping group size and travel type in mind
- Suggest improvements, alternatives, local tips
- Keep answers clean and structured
"""
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}]
            )
            reply = response.choices[0].message.content
        except Exception as e:
            reply = f"Error: {str(e)}"
        return JsonResponse({"reply": reply})


# ─────────────────────────────────────────
# PDF DOWNLOAD — by trip_id (saved trip) or by POST result text
# ─────────────────────────────────────────
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from io import BytesIO

@login_required
def download_trip_pdf(request, pk):
    trip = get_object_or_404(TripItinerary, pk=pk, user=request.user)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20*mm, leftMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
        title=f"Itinerary - {trip.destination}",
    )

    # ── Styles ───────────────────────────────────────────
    styles = getSampleStyleSheet()

    s_brand = ParagraphStyle('brand',
        fontName='Helvetica', fontSize=7, textColor=colors.HexColor('#888888'),
        spaceAfter=4, leading=10, alignment=TA_CENTER)

    s_title = ParagraphStyle('title',
        fontName='Helvetica-Bold', fontSize=28, textColor=colors.HexColor('#1c1c1c'),
        spaceAfter=4, leading=32, alignment=TA_CENTER)

    s_subtitle = ParagraphStyle('subtitle',
        fontName='Helvetica-Oblique', fontSize=13, textColor=colors.HexColor('#555555'),
        spaceAfter=6, leading=18, alignment=TA_CENTER)

    s_meta_key = ParagraphStyle('meta_key',
        fontName='Helvetica', fontSize=7, textColor=colors.HexColor('#999999'),
        spaceAfter=1, leading=10)

    s_meta_val = ParagraphStyle('meta_val',
        fontName='Helvetica', fontSize=11, textColor=colors.HexColor('#1c1c1c'),
        spaceAfter=8, leading=15)

    s_section = ParagraphStyle('section',
        fontName='Helvetica-Bold', fontSize=14, textColor=colors.HexColor('#1c1c1c'),
        spaceBefore=14, spaceAfter=6, leading=18)

    s_day_heading = ParagraphStyle('day_heading',
        fontName='Helvetica-Bold', fontSize=11, textColor=colors.HexColor('#1c1c1c'),
        spaceBefore=10, spaceAfter=4, leading=15,
        leftIndent=8, borderPad=4)

    s_body = ParagraphStyle('body',
        fontName='Helvetica', fontSize=9.5, textColor=colors.HexColor('#333333'),
        spaceAfter=3, leading=15, leftIndent=12)

    s_advice_head = ParagraphStyle('advice_head',
        fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor('#1c1c1c'),
        spaceAfter=4, leading=14)

    s_footer = ParagraphStyle('footer',
        fontName='Helvetica-Oblique', fontSize=7, textColor=colors.HexColor('#bbbbbb'),
        spaceAfter=0, leading=10, alignment=TA_CENTER)

    # ── Build content ────────────────────────────────────
    story = []

    # Cover
    story.append(Spacer(1, 18*mm))
    story.append(Paragraph("AI TRAVEL PLANNER", s_brand))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("Travel", s_title))
    story.append(Paragraph("Itinerary", s_subtitle))
    story.append(Spacer(1, 8*mm))
    story.append(HRFlowable(width="60%", thickness=1.5, color=colors.HexColor('#1c1c1c'), hAlign='CENTER'))
    story.append(Spacer(1, 8*mm))

    # Route / destination
    if trip.origin:
        story.append(Paragraph(f"{trip.origin}  →  {trip.destination}", s_subtitle))
    else:
        story.append(Paragraph(trip.destination, s_subtitle))

    story.append(Spacer(1, 10*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e0e0e0'), hAlign='CENTER'))

    # Meta info
    meta = [
        ("Destination", trip.destination),
        ("Duration",    f"{trip.days} days"),
        ("Travellers",  f"{trip.members} · {trip.travel_type}"),
        ("Budget",      f"Rs. {trip.budget}"),
        ("Budget Status", "Sufficient" if trip.is_budget_sufficient else "May be tight — see advice"),
    ]
    if trip.origin:
        meta.insert(0, ("Origin", trip.origin))

    story.append(Spacer(1, 4*mm))
    for key, val in meta:
        story.append(Paragraph(key.upper(), s_meta_key))
        story.append(Paragraph(val, s_meta_val))

    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e0e0e0'), hAlign='CENTER'))
    story.append(Spacer(1, 60*mm))
    story.append(Paragraph("Generated by AI Travel Planner · Safe Travels", s_footer))

    # Page break — new page for itinerary
    from reportlab.platypus import PageBreak
    story.append(PageBreak())

    # ── Parse result_text ────────────────────────────────
    lines        = trip.result_text.splitlines()
    in_advice    = False
    current_day  = None
    has_itinerary = False

    story.append(Paragraph("Day-by-Day Plan", s_section))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#1c1c1c'), hAlign='CENTER'))
    story.append(Spacer(1, 4*mm))

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        llow = stripped.lower()

        # Budget advice block
        if llow.startswith("budget advice"):
            if current_day:
                current_day = None
            in_advice = True
            story.append(Spacer(1, 6*mm))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#cccccc'), hAlign='CENTER'))
            story.append(Spacer(1, 4*mm))
            story.append(Paragraph("Budget Advice", s_advice_head))
            continue

        # Skip summary/overview lines
        SKIP_PREFIXES = ("destination", "origin", "trip", "number of",
                         "budget", "is budget", "estimated", "travel type", "members")
        if not in_advice and not llow.startswith("day") and any(llow.startswith(p) for p in SKIP_PREFIXES):
            continue

        # Day heading
        if not in_advice and llow.startswith("day") and len(stripped) < 120:
            has_itinerary = True
            current_day = stripped
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph(stripped, s_day_heading))
            story.append(HRFlowable(width="40%", thickness=0.5,
                                     color=colors.HexColor('#cccccc'), hAlign='LEFT'))
            continue

        # Body line
        safe = stripped.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        story.append(Paragraph(safe, s_body))

    # Footer
    story.append(Spacer(1, 12*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e0e0e0'), hAlign='CENTER'))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph("AI Travel Planner · Safe Travels", s_footer))

    # ── Build & return ───────────────────────────────────
    doc.build(story)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="itinerary-{trip.destination}.pdf"'
    )
    return response

@login_required(login_url='/login/')
def profile_view(request):
    trips = TripItinerary.objects.filter(user=request.user).order_by('-created_at')
    total_days = sum(t.days for t in trips)
    unique_destinations = trips.values('destination').distinct().count()

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_name":
            first_name = request.POST.get("first_name", "").strip()
            last_name  = request.POST.get("last_name", "").strip()
            request.user.first_name = first_name
            request.user.last_name  = last_name
            request.user.save()
            messages.success(request, "Name updated successfully.")
        elif action == "change_password":
            old_pw = request.POST.get("old_password", "")
            new_pw1 = request.POST.get("new_password1", "")
            new_pw2 = request.POST.get("new_password2", "")
            if not request.user.check_password(old_pw):
                messages.error(request, "Current password is incorrect.")
            elif new_pw1 != new_pw2:
                messages.error(request, "New passwords do not match.")
            elif len(new_pw1) < 8:
                messages.error(request, "Password must be at least 8 characters.")
            else:
                request.user.set_password(new_pw1)
                request.user.save()
                login(request, request.user)
                messages.success(request, "Password changed successfully.")
        return redirect('profile')

    return render(request, "profile.html", {
        "trips": trips,
        "total_days": total_days,
        "unique_destinations": unique_destinations,
    })