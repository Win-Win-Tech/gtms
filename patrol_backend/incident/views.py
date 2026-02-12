from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from django.utils import timezone
from django.db.models import Q
from django.http import HttpResponse
from datetime import datetime, timedelta
from io import BytesIO
from openpyxl import Workbook
from .models import incidentreport
from .serializers import IncidentSerializer
from twilio.rest import Client
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    get_user_today,
    get_user_now,
    convert_date_range_to_utc,
    to_user_timezone
)
import logging
import os
import cloudinary
import cloudinary.uploader

logger = logging.getLogger(__name__)

# Cloudinary configuration (can also be placed in settings.py)
# cloudinary.config(
#     cloud_name=settings.CLOUDINARY_CLOUD_NAME,
#     api_key=settings.CLOUDINARY_API_KEY,
#     api_secret=settings.CLOUDINARY_API_SECRET
# )

class IncidentReportView(APIView):
        parser_classes = [MultiPartParser]

        def post(self, request):
            data = request.data.copy()
            data['created_by'] = request.user.id
            data['location'] = request.user.location_id
            serializer = IncidentSerializer(data=data, context={'request': request})

            if serializer.is_valid():
                incident = serializer.save()

                # Upload media to Cloudinary
                media_urls = []
                photo_url = None
                video_url = None

                if incident.photo:
                    upload_result = cloudinary.uploader.upload(incident.photo.file)
                    photo_url = upload_result.get('secure_url')
                    media_urls.append(photo_url)

                if incident.video:
                    upload_result = cloudinary.uploader.upload(
                        incident.video.file,
                        resource_type="video"
                    )
                    video_url = upload_result.get('secure_url')
                    media_urls.append(video_url)

                # Convert timestamp to location timezone for display
                # Use incident's location timezone if available
                location_id = str(incident.location.id) if incident.location else None
                user_tz = get_user_timezone_from_request(request, location_id=location_id)
                created_on_user = to_user_timezone(incident.created_on, user_tz)

                whatsapp_body = (
                    f"🚨 Incident Alert 🚨\n"
                    f"Severity: {incident.severity}\n"
                    f"Description: {incident.incident_description}\n"
                    f"Ticket: {incident.ticket_number}\n"
                    f"Status: {incident.status}\n"
                    f"Timestamp: {created_on_user.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Photo: {os.path.basename(incident.photo.name) if incident.photo else 'N/A'}"
                )

                # Twilio notification is non-blocking for incident creation.
                # If Twilio auth/config fails, incident is still saved and API returns 201.
                try:
                    client = Client(settings.TWILIO_SID, settings.TWILIO_AUTH_TOKEN)

                    # Send WhatsApp message with photo or text
                    if photo_url:
                        client.messages.create(
                            body=whatsapp_body,
                            from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
                            to='whatsapp:' + settings.ADMIN_WHATSAPP,
                            media_url=[photo_url]
                        )
                    else:
                        client.messages.create(
                            body=whatsapp_body,
                            from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
                            to='whatsapp:' + settings.ADMIN_WHATSAPP
                        )

                    # Send video separately if present
                    if video_url:
                        client.messages.create(
                            body=f"🎥 Incident Video for Ticket {incident.ticket_number}",
                            from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
                            to='whatsapp:' + settings.ADMIN_WHATSAPP,
                            media_url=[video_url]
                        )

                    # Trigger phone call
                    client.calls.create(
                        twiml=f'<Response><Say>Alert! A {incident.severity} incident has been reported. Ticket {incident.ticket_number}.</Say></Response>',
                        to=settings.ADMIN_PHONE,
                        from_=settings.TWILIO_PHONE
                    )
                except Exception as exc:
                    logger.exception(
                        "[INCIDENT_REPORT] Twilio notification failed for ticket %s: %s",
                        incident.ticket_number,
                        str(exc),
                    )

                return Response(serializer.data, status=status.HTTP_201_CREATED)

            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# class IncidentReportView(APIView):
#     parser_classes = [MultiPartParser]

#     def post(self, request):
#         data = request.data.copy()
#         data['created_by'] = request.user.id
#         data['location'] = request.user.location_id
#         serializer = IncidentSerializer(data=data)

#         if serializer.is_valid():
#             incident = serializer.save()

#             # Upload media to Cloudinary
#             media_urls = []

#             if incident.photo:
# #               upload_result = cloudinary.uploader.upload(incident.photo.path)
#                 upload_result = cloudinary.uploader.upload(incident.photo.file)
#                 photo_url = upload_result.get('secure_url')
#                 media_urls.append(photo_url)
#             else:
#                 photo_url = "N/A"

#             if incident.video:
#                 upload_result = cloudinary.uploader.upload(incident.video.path, resource_type="video")
#                 video_url = upload_result.get('secure_url')
#                 media_urls.append(video_url)

#             # Twilio client setup
#             client = Client(settings.TWILIO_SID, settings.TWILIO_AUTH_TOKEN)

#             whatsapp_body = (
#                 f"🚨 Incident Alert 🚨\n"
#                 f"Severity: {incident.severity}\n"
#                 f"Description: {incident.incident_description}\n"
#                 f"Ticket: {incident.ticket_number}\n"
#                 f"Status: {incident.status}\n"
#                 f"Timestamp: {incident.created_on.strftime('%Y-%m-%d %H:%M:%S')}\n"
#                 f"Photo: {os.path.basename(incident.photo.name) if incident.photo else 'N/A'}"
#             )

#             # Send WhatsApp message
#             message = client.messages.create(
#                 body=whatsapp_body,
#                 from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
#                 to='whatsapp:' + settings.ADMIN_WHATSAPP,
#                 media_url=media_urls if media_urls else None
#             )

#             #         #Trigger phone call

#             client.calls.create(
#                  twiml=f'<Response><Say>Alert! A {incident.severity} incident has been reported. Ticket {incident.ticket_number}.</Say></Response>',
#                  to=settings.ADMIN_PHONE,
#                  from_=settings.TWILIO_PHONE
#             )

#             # Log message details
#             print("Message SID:", message.sid)
#             print("Status:", message.status)
#             print("To:", message.to)
#             print("From:", message.from_)
#             print("Date Created:", message.date_created)
#             print("Media:", message.media)

#             return Response(serializer.data, status=status.HTTP_201_CREATED)

#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    #/////////////29-Oct/////////////
    # def post(self, request):
    #     data = request.data.copy()
    #     data['created_by'] = request.user.id
    #     data['location'] = request.user.location_id
    #     serializer = IncidentSerializer(data=data)
    #     if serializer.is_valid():
    #         incident = serializer.save()

    #         # Twilio client setup
    #         client = Client(settings.TWILIO_SID, settings.TWILIO_AUTH_TOKEN)
    #         photo_filename = os.path.basename(incident.photo.name) if incident.photo else "N/A"            #Send WhatsApp message
    #         whatsapp_body = (
    #             f"🚨 Incident Alert 🚨\n"
    #             f"Severity: {incident.severity}\n"
    #             f"Description: {incident.incident_description}\n"
    #             f"Ticket: {incident.ticket_number}\n"
    #             f"Status: {incident.status}\n"
    #             f"Timestamp: {incident.created_on.strftime('%Y-%m-%d %H:%M:%S')}"
    #             f"Photo: {photo_filename}"
    #         )

    #         # Media URLs (must be HTTPS and publicly accessible)
    #         media_urls = []
    #         if incident.photo:
    #            media_urls=[incident.photo]  # e.g., 'http://localhost:8000/media/incidents/EAF399B3-396/1.jpg'
    #         else:
    #            media_urls = None 
    #         if incident.video:
    #            media_urls.append(incident.video)  # e.g., 'https://yourdomain.com/path/to/video.mp4'

    #         #media_url = incident.photo.url if incident.photo else None
    #         #media_url = [request.build_absolute_uri(incident.photo.url)] if incident.photo else None

    #         #print ("media_urls:", media_urls)

    #         message = client.messages.create(
    #              body=whatsapp_body,
    #              from_='whatsapp:' + settings.TWILIO_WHATSAPP_NUMBER,
    #              to='whatsapp:' + settings.ADMIN_WHATSAPP,
    #              media_url=media_urls                
    #         )
    #         # # Print response details
    #         print("Message SID:", message.sid)
    #         print("Status:", message.status)
    #         print("To:", message.to)
    #         print("From:", message.from_)
    #         print("Date Created:", message.date_created)
    #         print("Media:", message.media)


    #         #Trigger phone call

    #         # client.calls.create(
    #         #      twiml=f'<Response><Say>Alert! A {incident.severity} incident has been reported. Ticket {incident.ticket_number}.</Say></Response>',
    #         #      to=settings.ADMIN_PHONE,
    #         #      from_=settings.TWILIO_PHONE
    #         # )

    #         return Response(serializer.data, status=status.HTTP_201_CREATED)
    #     return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    #/////////////29-Oct/////////////


class IncidentAssignView(APIView):
    def post(self, request, ticket_number):
        try:
            incident = incidentreport.objects.get(ticket_number=ticket_number)
        except incidentreport.DoesNotExist:
            return Response({'error': 'Incident not found'}, status=status.HTTP_404_NOT_FOUND)

        incident.assigned_by = request.user
        incident.assigned_to_id = request.data.get('assigned_to')
        incident.assigned_on = timezone.now()
        incident.save()
        
        # Return serialized incident with timezone conversion
        serializer = IncidentSerializer(incident, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

class IncidentResolveView(APIView):
    def post(self, request, ticket_number):
        try:
            incident = incidentreport.objects.get(ticket_number=ticket_number)
        except incidentreport.DoesNotExist:
            return Response({'error': 'Incident not found'}, status=status.HTTP_404_NOT_FOUND)

        incident.resolved_by = request.user
        incident.resolved_on = timezone.now()
        incident.closure_description = request.data.get('closure_description', '')
        incident.save()
        
        # Return serialized incident with timezone conversion
        serializer = IncidentSerializer(incident, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


# Duplicate imports removed - using imports from top of file

# class IncidentFilterView(APIView):
#     def get(self, request):
#         # Get query parameters
#         date_filter = request.query_params.get('date_filter', '').lower()
#         #status_filter = request.query_params.get('status', '').capitalize()
#         status_filter = request.query_params.get('status', '').title()
#         severity_filter = request.query_params.get('severity', '').capitalize()
#         start_date = request.query_params.get('start_date')
#         end_date = request.query_params.get('end_date')

#                 # Base queryset
#         queryset = incidentreport.objects.all()

#         # Apply status filter
#         if status_filter in ['Open', 'In-Progress', 'Closed']:
#             queryset = queryset.filter(status=status_filter)

#         if severity_filter in ['Low', 'Medium', 'High']:
#             queryset = queryset.filter(severity=severity_filter)

#         # Apply date filter
#         now = timezone.now()
#         if date_filter == 'today':
#             queryset = queryset.filter(created_on__date=now.date())
#         elif date_filter == 'this_week':
#             start_of_week = now - datetime.timedelta(days=now.weekday())
#             queryset = queryset.filter(created_on__date__gte=start_of_week.date())
#         elif date_filter == 'this_month':
#             queryset = queryset.filter(created_on__year=now.year, created_on__month=now.month)
#         elif date_filter == 'custom' and start_date and end_date:
#             try:
#                 start = datetime.datetime.strptime(start_date, '%Y-%m-%d')
#                 end = datetime.datetime.strptime(end_date, '%Y-%m-%d') + datetime.timedelta(days=1)
#                 queryset = queryset.filter(created_on__range=(start, end))
#             except ValueError:
#                 return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

#         # Serialize and return
#         serializer = IncidentSerializer(queryset, many=True)
#         return Response(serializer.data, status=status.HTTP_200_OK)


class IncidentFilterView(APIView):
    def get(self, request):
        # Get query parameters
        date_filter = request.query_params.get('date_filter', '').lower()
        status_filter = request.query_params.get('status', '').title()
        severity_filter = request.query_params.get('severity', '').capitalize()
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        # New filters
        location_id = request.query_params.get('location_id')
        assigned_to_id = request.query_params.get('assigned_to')
        created_by_id = request.query_params.get('created_by')
        checkpoint_id = request.query_params.get('checkpoint_id')

        # Base queryset
        queryset = incidentreport.objects.all()

        # Apply status filter
        if status_filter in ['Open', 'In-Progress', 'Closed']:
            queryset = queryset.filter(status=status_filter)

        # Apply severity filter
        if severity_filter in ['Low', 'Medium', 'High']:
            queryset = queryset.filter(severity=severity_filter)

        # Apply date filter - use location timezone if location_id filter is provided
        # This ensures superadmins see dates in the filtered location's timezone
        filter_location_id = location_id if location_id else None
        user_tz = get_user_timezone_from_request(request, location_id=filter_location_id)
        user_now = get_user_now(user_tz)
        user_today = user_now.date()
        
        if date_filter == 'today':
            # Convert today to UTC date range for query
            start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'this_week':
            start_of_week = user_today - timedelta(days=user_today.weekday())
            end_of_week = start_of_week + timedelta(days=6)
            start_utc, end_utc = convert_date_range_to_utc(start_of_week, end_of_week, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'this_month':
            # Get month boundaries in user timezone
            start_of_month = user_today.replace(day=1)
            if user_today.month == 12:
                end_of_month = user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end_of_month = user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1)
            start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'custom' and start_date and end_date:
            try:
                # Parse dates as user timezone dates
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
                end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
                # Convert to UTC range for query
                start_utc, end_utc = convert_date_range_to_utc(start_date_obj, end_date_obj, user_tz)
                queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        # Apply new filters
        if location_id:
            queryset = queryset.filter(location_id=location_id)
        if assigned_to_id:
            queryset = queryset.filter(assigned_to_id=assigned_to_id)
        if created_by_id:
            queryset = queryset.filter(created_by_id=created_by_id)
        if checkpoint_id:
            queryset = queryset.filter(checkpoint_id=checkpoint_id)

        # Serialize and return
        serializer = IncidentSerializer(queryset, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class IncidentExportExcelView(APIView):
    """
    API endpoint that generates and returns an Excel file for incident reports.
    Includes all filters from IncidentFilterView plus additional Excel-only fields.
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        # Get query parameters (same as IncidentFilterView)
        date_filter = request.query_params.get('date_filter', '').lower()
        status_filter = request.query_params.get('status', '').title()
        severity_filter = request.query_params.get('severity', '').capitalize()
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        # Additional filters
        location_id = request.query_params.get('location_id')
        assigned_to_id = request.query_params.get('assigned_to')
        created_by_id = request.query_params.get('created_by')
        checkpoint_id = request.query_params.get('checkpoint_id')

        # Base queryset
        queryset = incidentreport.objects.select_related(
            'created_by', 'assigned_by', 'assigned_to', 'resolved_by', 
            'location', 'checkpoint'
        ).all()

        # Apply status filter
        if status_filter in ['Open', 'In-Progress', 'Closed']:
            queryset = queryset.filter(status=status_filter)

        # Apply severity filter
        if severity_filter in ['Low', 'Medium', 'High']:
            queryset = queryset.filter(severity=severity_filter)

        # Apply date filter - use location timezone if location_id filter is provided
        filter_location_id = location_id if location_id else None
        user_tz = get_user_timezone_from_request(request, location_id=filter_location_id)
        user_now = get_user_now(user_tz)
        user_today = user_now.date()
        
        if date_filter == 'today':
            start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'this_week':
            start_of_week = user_today - timedelta(days=user_today.weekday())
            end_of_week = start_of_week + timedelta(days=6)
            start_utc, end_utc = convert_date_range_to_utc(start_of_week, end_of_week, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'this_month':
            start_of_month = user_today.replace(day=1)
            if user_today.month == 12:
                end_of_month = user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end_of_month = user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1)
            start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'custom' and start_date and end_date:
            try:
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
                end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
                start_utc, end_utc = convert_date_range_to_utc(start_date_obj, end_date_obj, user_tz)
                queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        # Apply additional filters
        if location_id:
            queryset = queryset.filter(location_id=location_id)
        if assigned_to_id:
            queryset = queryset.filter(assigned_to_id=assigned_to_id)
        if created_by_id:
            queryset = queryset.filter(created_by_id=created_by_id)
        if checkpoint_id:
            queryset = queryset.filter(checkpoint_id=checkpoint_id)

        # Create Excel workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Incident Report"

        # Header row with all fields including Excel-only fields
        headers = [
            'Ticket Number', 'Severity', 'Status', 'Location', 'Checkpoint',
            'Created On', 'Created By', 
            'Assigned On', 'Assigned By', 'Assigned To',
            'Closed On', 'Closed By',
            'Time Difference (Created-Assigned)', 
            'Time Difference (Assigned-Closed)',
            'Time Difference (Created-Closed)',
            'Description', 'Closure Description', 'SLA Status'
        ]
        ws.append(headers)

        # Helper function to format time difference
        def format_time_difference(start_time, end_time):
            """Format time difference as hours and minutes"""
            if not start_time or not end_time:
                return ""
            diff = end_time - start_time
            total_seconds = int(diff.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            if hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"

        # Helper function to calculate SLA status
        def calculate_sla_status(incident):
            """Calculate SLA status based on severity and closure time"""
            if not incident.resolved_on or not incident.created_on:
                return "N/A"
            
            # Define SLA thresholds in hours based on severity
            sla_thresholds = {
                'High': 8,    # 8 hours for High severity
                'Medium': 16,  # 16 hours for Medium severity
                'Low': 24      # 24 hours for Low severity
            }
            
            threshold_hours = sla_thresholds.get(incident.severity, 8)
            time_diff = incident.resolved_on - incident.created_on
            hours_taken = time_diff.total_seconds() / 3600
            
            if hours_taken <= threshold_hours:
                return "Met"
            else:
                return "Not Met"

        # Add data rows
        for incident in queryset:
            # Get location timezone for this incident
            incident_location_id = str(incident.location.id) if incident.location else None
            incident_tz = get_user_timezone_from_request(request, location_id=incident_location_id)
            
            # Convert timestamps to user timezone
            created_on_tz = to_user_timezone(incident.created_on, incident_tz) if incident.created_on else None
            assigned_on_tz = to_user_timezone(incident.assigned_on, incident_tz) if incident.assigned_on else None
            closed_on_tz = to_user_timezone(incident.resolved_on, incident_tz) if incident.resolved_on else None
            
            # Calculate time differences (using UTC times for accurate calculation)
            time_diff_created_assigned = format_time_difference(incident.created_on, incident.assigned_on)
            time_diff_assigned_closed = format_time_difference(incident.assigned_on, incident.resolved_on)
            time_diff_created_closed = format_time_difference(incident.created_on, incident.resolved_on)
            
            # Get user names
            created_by_name = incident.created_by.name if incident.created_by else ""
            assigned_by_name = incident.assigned_by.name if incident.assigned_by else ""
            assigned_to_name = incident.assigned_to.name if incident.assigned_to else ""
            closed_by_name = incident.resolved_by.name if incident.resolved_by else ""
            
            # Get location and checkpoint names
            location_name = incident.location.name if incident.location else ""
            checkpoint_name = incident.checkpoint.label if incident.checkpoint else ""
            
            # Calculate SLA status
            sla_status = calculate_sla_status(incident)
            
            # Format datetime strings
            created_on_str = created_on_tz.strftime("%Y-%m-%d %H:%M:%S") if created_on_tz else ""
            assigned_on_str = assigned_on_tz.strftime("%Y-%m-%d %H:%M:%S") if assigned_on_tz else ""
            closed_on_str = closed_on_tz.strftime("%Y-%m-%d %H:%M:%S") if closed_on_tz else ""
            
            ws.append([
                incident.ticket_number,
                incident.severity,
                incident.status,
                location_name,
                checkpoint_name,
                created_on_str,
                created_by_name,
                assigned_on_str,
                assigned_by_name,
                assigned_to_name,
                closed_on_str,
                closed_by_name,
                time_diff_created_assigned,
                time_diff_assigned_closed,
                time_diff_created_closed,
                incident.incident_description or "",
                incident.closure_description or "",
                sla_status
            ])

        # Save to in-memory buffer
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        # Return Excel file as HTTP response
        excel_content = buffer.getvalue()
        
        response = HttpResponse(
            excel_content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
        # Generate a meaningful filename based on filter
        if date_filter == 'custom' and start_date and end_date:
            filename = f"incident_report_{start_date}_{end_date}.xlsx"
        elif date_filter:
            filename = f"incident_report_{date_filter}_{timezone.now().strftime('%Y%m%d')}.xlsx"
        else:
            filename = f"incident_report_{timezone.now().strftime('%Y%m%d')}.xlsx"
        
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class MyTicketsExportExcelView(APIView):
    """
    API endpoint that generates and returns an Excel file for MyTickets (assigned to current user).
    Includes all filters from IncidentFilterView plus additional Excel-only fields.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Get query parameters (same as IncidentFilterView)
        date_filter = request.query_params.get('date_filter', '').lower()
        status_filter = request.query_params.get('status', '').title()
        severity_filter = request.query_params.get('severity', '').capitalize()
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        # Base queryset - filter by assigned_to current user
        queryset = incidentreport.objects.select_related(
            'created_by', 'assigned_by', 'assigned_to', 'resolved_by',
            'location', 'checkpoint'
        ).filter(assigned_to=request.user).all()

        # Apply status filter
        if status_filter in ['Open', 'In-Progress', 'Closed']:
            queryset = queryset.filter(status=status_filter)

        # Apply severity filter
        if severity_filter in ['Low', 'Medium', 'High']:
            queryset = queryset.filter(severity=severity_filter)

        # Apply date filter - use location timezone if location_id filter is provided
        filter_location_id = None  # MyTickets doesn't filter by location
        user_tz = get_user_timezone_from_request(request, location_id=filter_location_id)
        user_now = get_user_now(user_tz)
        user_today = user_now.date()

        if date_filter == 'today':
            start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'this_week':
            start_of_week = user_today - timedelta(days=user_today.weekday())
            end_of_week = start_of_week + timedelta(days=6)
            start_utc, end_utc = convert_date_range_to_utc(start_of_week, end_of_week, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'this_month':
            start_of_month = user_today.replace(day=1)
            if user_today.month == 12:
                end_of_month = user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end_of_month = user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1)
            start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        elif date_filter == 'custom' and start_date and end_date:
            try:
                start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
                end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
                start_utc, end_utc = convert_date_range_to_utc(start_date_obj, end_date_obj, user_tz)
                queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
            except ValueError:
                return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        # Create Excel workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "My Tickets"

        # Header row with all fields including Excel-only fields
        headers = [
            'Ticket Number', 'Severity', 'Status', 'Location', 'Checkpoint',
            'Created On', 'Created By',
            'Assigned On', 'Assigned By', 'Assigned To',
            'Closed On', 'Closed By',
            'Time Difference (Created-Assigned)',
            'Time Difference (Assigned-Closed)',
            'Time Difference (Created-Closed)',
            'Description', 'Closure Description', 'SLA Status'
        ]
        ws.append(headers)

        # Helper function to format time difference
        def format_time_difference(start_time, end_time):
            """Format time difference as hours and minutes"""
            if not start_time or not end_time:
                return ""
            diff = end_time - start_time
            total_seconds = int(diff.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            if hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"

        # Helper function to calculate SLA status
        def calculate_sla_status(incident):
            """Calculate SLA status based on severity and closure time"""
            if not incident.resolved_on or not incident.created_on:
                return "N/A"

            # Define SLA thresholds in hours based on severity
            sla_thresholds = {
                'High': 2,    # 2 hours for High severity
                'Medium': 4,  # 4 hours for Medium severity
                'Low': 8      # 8 hours for Low severity
            }

            threshold_hours = sla_thresholds.get(incident.severity, 8)
            time_diff = incident.resolved_on - incident.created_on
            hours_taken = time_diff.total_seconds() / 3600

            if hours_taken <= threshold_hours:
                return "Met"
            else:
                return "Not Met"

        # Add data rows
        for incident in queryset:
            # Get location timezone for this incident
            incident_location_id = str(incident.location.id) if incident.location else None
            incident_tz = get_user_timezone_from_request(request, location_id=incident_location_id)

            # Convert timestamps to user timezone
            created_on_tz = to_user_timezone(incident.created_on, incident_tz) if incident.created_on else None
            assigned_on_tz = to_user_timezone(incident.assigned_on, incident_tz) if incident.assigned_on else None
            closed_on_tz = to_user_timezone(incident.resolved_on, incident_tz) if incident.resolved_on else None

            # Calculate time differences (using UTC times for accurate calculation)
            time_diff_created_assigned = format_time_difference(incident.created_on, incident.assigned_on)
            time_diff_assigned_closed = format_time_difference(incident.assigned_on, incident.resolved_on)
            time_diff_created_closed = format_time_difference(incident.created_on, incident.resolved_on)

            # Get user names
            created_by_name = incident.created_by.name if incident.created_by else ""
            assigned_by_name = incident.assigned_by.name if incident.assigned_by else ""
            assigned_to_name = incident.assigned_to.name if incident.assigned_to else ""
            closed_by_name = incident.resolved_by.name if incident.resolved_by else ""

            # Get location and checkpoint names
            location_name = incident.location.name if incident.location else ""
            checkpoint_name = incident.checkpoint.label if incident.checkpoint else ""

            # Calculate SLA status
            sla_status = calculate_sla_status(incident)

            # Format datetime strings
            created_on_str = created_on_tz.strftime("%Y-%m-%d %H:%M:%S") if created_on_tz else ""
            assigned_on_str = assigned_on_tz.strftime("%Y-%m-%d %H:%M:%S") if assigned_on_tz else ""
            closed_on_str = closed_on_tz.strftime("%Y-%m-%d %H:%M:%S") if closed_on_tz else ""

            ws.append([
                incident.ticket_number,
                incident.severity,
                incident.status,
                location_name,
                checkpoint_name,
                created_on_str,
                created_by_name,
                assigned_on_str,
                assigned_by_name,
                assigned_to_name,
                closed_on_str,
                closed_by_name,
                time_diff_created_assigned,
                time_diff_assigned_closed,
                time_diff_created_closed,
                incident.incident_description or "",
                incident.closure_description or "",
                sla_status
            ])

        # Save to in-memory buffer
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        # Return Excel file as HTTP response
        excel_content = buffer.getvalue()

        response = HttpResponse(
            excel_content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # Generate a meaningful filename based on filter
        if date_filter == 'custom' and start_date and end_date:
            filename = f"mytickets_{start_date}_{end_date}.xlsx"
        elif date_filter:
            filename = f"mytickets_{date_filter}_{timezone.now().strftime('%Y%m%d')}.xlsx"
        else:
            filename = f"mytickets_{timezone.now().strftime('%Y%m%d')}.xlsx"

        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

