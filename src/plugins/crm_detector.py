def is_crm_action(message: str) -> bool:
    """
    Determines if the user message is attempting a CRM action
    (creating a ticket, checking status) versus a general knowledge query.
    """
    message = message.lower()
    
    # 1. Check for digits (National IDs or Ticket numbers)
    if any(char.isdigit() for char in message):
        return True
        
    # 2. Check for exact CRM-specific phrases
    crm_keywords = [
        "تقديم شكوى", 
        "ارفع شكوى",
        "سجل شكوى",
        "متابعة شكوى", 
        "حالة شكوى",
        "رقم شكوتي",
        "رقم البلاغ",
        "أتابع الطلب"
    ]
    
    for keyword in crm_keywords:
        if keyword in message:
            return True
            
    # 3. Check for action verbs combined with complaint nouns
    action_verbs = ["قدم", "ارفع", "سجل", "تابع", "شوف", "شيك", "عندي"]
    nouns = ["شكوى", "بلاغ", "طلب", "اعتراض", "مشكلة"]
    
    for verb in action_verbs:
        for noun in nouns:
            if f"{verb} {noun}" in message or f"{verb} لي {noun}" in message:
                return True

    # If it fails all checks, it's a general knowledge query
    return False