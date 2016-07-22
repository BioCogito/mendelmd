from django.shortcuts import render

from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from django.contrib.auth.decorators import login_required
from django.utils.datastructures import SortedDict
from variants.models import *

import pickle
import json

@login_required
def view(request, variant_id):
    variant = get_object_or_404(Variant, pk=variant_id)

    variant.info = json.loads(variant.info)

    new_dict = SortedDict()
    key_list = list(variant.info.keys())
    key_list.sort()
    for key in key_list:
        new_dict[key] = variant.info[key]

    variant.info = new_dict
    print(type(variant.info))

    return render_to_response('variants/view.html', {'variant': variant}, context_instance=RequestContext(request))