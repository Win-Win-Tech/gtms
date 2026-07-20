# Visitor Management — Iteration 1 (Manual Entry) API curls
#
# Base URL example: http://localhost:8000
# Auth: Authorization: Bearer <access_token>
#
# Visitor types:  contractor | client | delivery | guest | other
# Entry status:   open | checked_in | checked_out | cancelled

# --- Search visitor by IC (location-scoped) ---
curl -X GET "http://localhost:8000/visitors/search/?ic_number=900101145678" \
  -H "Authorization: Bearer <TOKEN>"

# Superadmin with location:
curl -X GET "http://localhost:8000/visitors/search/?ic_number=900101145678&location_id=<LOCATION_UUID>" \
  -H "Authorization: Bearer <TOKEN>"

# --- Walk-in check-in (multipart, manual fields) ---
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "phone_number=0123456789" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "purpose_of_visit=Meeting" \
  -F "vehicle_number=ABC1234" \
  -F "remarks=" \
  -F "visitor_photo=@/path/to/photo.jpg" \
  -F "id_proof=@/path/to/id.jpg" \
  -F "vehicle_photo=@/path/to/plate.jpg" \
  -F "additional_images=@/path/to/extra1.jpg" \
  -F "additional_images=@/path/to/extra2.jpg"

# Image rules:
#   visitor_photo, id_proof, vehicle_photo, exit_photo → single file each
#   additional_images → repeat the same field name for multiple files

# Superadmin may also pass:
#   -F "location_id=<LOCATION_UUID>"

# --- Checkout ---
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/checkout/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "exit_photo=@/path/to/exit.jpg"

# --- History list ---
curl -X GET "http://localhost:8000/visitors/entries/?date_filter=today&status=checked_in&search=Ahmad" \
  -H "Authorization: Bearer <TOKEN>"

# date_filter: today | this_week | this_month | custom | all
# custom also needs: start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
# status: all | checked_in | checked_out | open | cancelled

# --- Excel export (same filters) ---
curl -X GET "http://localhost:8000/visitors/entries/export/?date_filter=today" \
  -H "Authorization: Bearer <TOKEN>" \
  -o visitor_entries.xlsx
