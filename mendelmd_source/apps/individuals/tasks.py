# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from celery.task import Task
from celery.registry import tasks
from django.db import transaction
from django.core.files import File
from celery import task

from individuals.models import *

from variants.models import *

# from mysql_bulk_insert import bulk_insert
from django.shortcuts import render_to_response, get_object_or_404
import os
import datetime
from django.core.mail import send_mail
# from snpedia.models import *
from diseases.models import HGMDMutation
from django.conf import settings

import zipfile
import gzip
import pickle

from collections import OrderedDict

from mongoengine import *

import json
import vcf

from datetime import timedelta

@task()
def clean_individuals():
    print("Running periodic task!")
    individuals = Individual.objects.filter(user=None)
    for individual in individuals:
        time_difference = datetime.datetime.now()-individual.creation_date
        if time_difference.days > 0:
            #delete individuals
            os.system('rm -rf %s/genomes/public/%s' % (settings.BASE_DIR, individual_id))
            individual.delete()

@task()
def VerifyVCF(individual_id):
    print('Verify VCF...')
    
    individual = get_object_or_404(Individual, pk=individual_id)
    print(individual.vcf_file)
    filename = str(individual.vcf_file.name.split('/')[-1])

    if individual.user:
        path  = '%s/genomes/%s/%s' % (settings.BASE_DIR, individual.user.username.lower(), individual.id)
    else:
        path  = '%s/genomes/public/%s' % (settings.BASE_DIR, individual.id)

    new_path = '/'.join(path.split('/')[:-1])
    print(new_path)


    os.chdir(path)

    if filename.endswith('.vcf'):
        command = 'cp %s sample.vcf' % (filename)
        os.system(command)
    if filename.endswith('.gz'):
        command = 'gunzip -c -d %s > sample.vcf' % (filename)
        os.system(command)
    if filename.endswith('.zip'):
        command = 'unzip -p %s > sample.vcf' % (filename)
        os.system(command)
    if filename.endswith('.rar'):
        command = 'unrar e %s' % (filename)
        os.system(command)
        #now change filename to sample.vcf
        command = 'mv %s sample.vcf' % (filename.replace('.rar', ''))
        os.system(command)


    vcf_reader = vcf.Reader(open('sample.vcf', 'r'))
    n_samples = len(vcf_reader.samples)
    print('n_samples', n_samples)

    if n_samples > 1:
        #extract individuals and create new users
        for sample in vcf_reader.samples:
            print(sample)
            command = "bcftools view -c 1 -s %s sample.vcf  > %s.vcf" % (sample, sample)
            print(command)
            os.system(command)
        #now rename original sample
        first_sample = vcf_reader.samples[0]
        original_name = individual.name
        individual.name += ' %s' % (first_sample)
        individual.vcf_file = "%s/%s/%s.vcf" % (new_path, individual.id, first_sample)
        individual.save()
        AnnotateVariants.delay(individual.id)
        #create other samples
        for sample in vcf_reader.samples[1:]:
            print(sample)
            new_individual = Individual.objects.create(user=individual.user, status='new')
            new_individual.name = original_name + ' %s' % (sample)
            new_individual.save()
            output_folder = '../%s' % (new_individual.id)
            if not os.path.exists(output_folder):
                os.makedirs(output_folder)
                os.chmod(output_folder, 0o777)
            command = 'mv %s.vcf %s' % (sample, output_folder)
            os.system(command)
            new_individual.vcf_file = '%s/%s/%s.vcf' % (new_path, new_individual.id, sample)
            new_individual.save()
            AnnotateVariants.delay(new_individual.id)
    else:
        AnnotateVariants.delay(individual_id)
    #check if VCF is multisample
    #if so extract individuals and create other individual models
    #if not send it to be annotated

@task()
def AnnotateVariants(individual_id):

    print('Annotation Started!!!')
    start = datetime.datetime.now()

    individual = get_object_or_404(Individual, pk=individual_id)

    #chdir into folder
    # print 'individual.vcf_file.name'
    # print individual.vcf_file.name
    if individual.user:
        path  = '%s/genomes/%s/%s' % (settings.BASE_DIR, individual.user.username, individual.id)
        email = individual.user.email
    else:
        path  = '%s/genomes/public/%s' % (settings.BASE_DIR, individual.id)
        email = 'raonyguimaraes@gmail.com'
    orig_path = os.getcwd()
    #change to path for the individual folder
    os.chdir(path)
    # print(os.getcwd())

    #delete annotation folder before start annotating
    command = 'rm -rf ann_*' 
    os.system(command)

    filename = str(individual.vcf_file.name.split('/')[-1])

    #deal with different types of compressed files 
    #ex. zip, vcf, gz, rar
    #check if user uploaded a compressed vcf
    if filename.endswith('.vcf'):
        command = 'cp %s sample.vcf' % (filename)
        os.system(command)        
    if filename.endswith('.gz'):
        command = 'gunzip -c -d %s > sample.vcf' % (filename)
        os.system(command)
    if filename.endswith('.zip'):
        command = 'unzip -p %s > sample.vcf' % (filename)
        os.system(command)
    if filename.endswith('.rar'):
        command = 'unrar e %s' % (filename)
        os.system(command)
        #now change filename to sample.vcf
        command = 'mv %s sample.vcf' % (filename.replace('.rar', ''))
        os.system(command)


    #     individual.vcf_file.name = individual.vcf_file.name.replace('.zip', '.vcf')


    # if individual.vcf_file.name.endswith("vcf"):
    # print(os.getcwd())

    command = 'pynnotator -i sample.vcf'
    os.system(command)

    #get sample name using pyvcf

    vcf_filename = os.path.splitext(os.path.basename(str(filename)))[0]

    # create a folder for the annotation if it doesn't exists, 
    # or delete and create if the folder already exists
    
    #first check if annotation succedded
    annotation_final_file = 'ann_sample/annotation.final.vcf'

    # print('checking destination ', annotation_final_file)
    stop = datetime.datetime.now()
    elapsed = stop - start

    individual.annotation_time = elapsed

    if os.path.exists(annotation_final_file):

        individual.status = 'annotated'
        #send email
        message = """The file %s was annotated with success!\n
It took %s to execute. \n
Now we need to insert this data to the database.
                """ % (individual.name, elapsed)
        send_mail('[Mendel,MD] Annotation Completed!', message, 'mendelmd1@gmail.com',
                  ['raonyguimaraes@gmail.com', email], fail_silently=False)
        #delete ann folder
        command = 'rm -rf ann_*'
        # os.system(command)
        #delete sample
        command = 'rm -rf sample.vcf'
        os.system(command)

        #zip, and delete annotation folder
        
        command = 'zip annotation.final.vcf.zip ann_sample/annotation.final.vcf'
        os.system(command)

        PopulateVariants.delay(individual.id)

    else:
        individual.status = 'failed'
        message = """The Individual %s failed to be annotated!\n
                It took %s to execute.
                """ % (individual.name, elapsed)
        send_mail('[Mendel,MD] Annotation Failed!', message, 'mendelmd1@gmail.com',
                  ['raonyguimaraes@gmail.com'], fail_silently=False)
        
    individual.save()
    os.chdir(settings.BASE_DIR)

def treat_float_max(float_string):
    max_value = -100
    values = float_string.split(',')
    for value in values:
        if value != '.':
            float_value = float(value)
            if float_value > max_value:
                max_value = float_value
    return max_value

def treat_float_min(float_string):
    minimum_value = 1
    values = float_string.split(',')
    for value in values:
        if value != '.':
            float_value = float(value)
            if float_value < minimum_value:
                minimum_value = float_value
    return minimum_value

def parse_vcf(line):

    '''This function is responsible for parsing the VCF file that is generated by our annotator pipeline and return a dictionary with the fields to be included in the database

    Receives variant_line and return variant dict
    '''
    variant = OrderedDict()
    #parse first VCF Lines
    variant_line = line.strip().split('\t')
    # print(variant_line)


    variant['chr'] = variant_line[0]
    #treat vcfs with chr on the begining of chromossome names
    if variant['chr'].startswith('chr'):
      variant['chr'] = variant['chr'].replace('chr', '')

    variant['pos'] = variant_line[1]
    variant['variant_id'] = variant_line[2]
    variant['ref'] = variant_line[3]
    variant['alt'] = variant_line[4]

    #this is form strange vcfs without qual values                
    if variant_line[5] == "-1" or variant_line[5] == ".":
        variant['qual'] = 0.0
    else:
        variant['qual'] = variant_line[5]

    variant['filter'] = variant_line[6]

    variant['format'] = variant_line[-2].split(':')

    variant['genotype_col'] = variant_line[-1].split(':')

    variant['genotype'] = variant['genotype_col'][0]
    
    # print 'genotype', variant['genotype_col'], variant['genotype']

    if variant['genotype'] != './.':
        #fix because of isaac variant caller, there is no DP
        if 'DP' in variant['format']:
            variant['read_depth'] = int(variant['genotype_col'][variant['format'].index('DP')])
        else:
            variant['read_depth'] = 0
    else:
        variant['read_depth'] = 0

    #annotations
    info = variant_line[7]
    string = info.split(';')

    #create a dict with all annotations at INFO field
    information = {}
    for element in string:                      
      #get all tags from line
      element = element.split('=')
      tag = element[0]
      if len(element) > 1:
        information[tag] = element[1]#.decode("utf-8", "ignore")
      else:
        information[tag] = tag

    if 'HET' in  information:
        variant['mutation_type'] = 'HET'
    elif 'HOM' in  information:
        variant['mutation_type'] = 'HOM'
    else:
        variant['mutation_type'] = None

    if 'VARTYPE' in  information:
        variant['vartype'] = information['VARTYPE']
    else:
        variant['vartype'] = None

    #serialize object with pickle
    variant['info'] = json.dumps(information)#.decode("utf-8", "ignore")

    # print 'information dict'
    # print information

    #parse VEP
    #Allele|Consequence|IMPACT|SYMBOL|Gene|Feature_type|Feature|BIOTYPE|EXON|INTRON|HGVSc|HGVSp|cDNA_position|CDS_position|Protein_position|Amino_acids|Codons|Existing_variation|DISTANCE|STRAND|FLAGS|SYMBOL_SOURCE|HGNC_ID|SIFT|PolyPhen

    if 'CSQ' in information:
        vep = OrderedDict()
        vep_list = information['CSQ'].split('|')

        vep['Allele'] = vep_list[0]
        vep['Consequence'] = vep_list[1]
        vep['IMPACT'] = vep_list[2]
        vep['SYMBOL'] = vep_list[3]
        vep['Gene'] = vep_list[4]
        vep['Feature_type'] = vep_list[5]
        vep['Feature'] = vep_list[6]
        vep['BIOTYPE'] = vep_list[7]
        vep['EXON'] = vep_list[8]
        vep['INTRON'] = vep_list[9]
        vep['HGVSc'] = vep_list[10]
        vep['HGVSp'] = vep_list[11]
        
        vep['cDNA_position'] = vep_list[12]
        vep['CDS_position'] = vep_list[13]
        vep['Protein_position'] = vep_list[14]
        vep['Amino_acids'] = vep_list[15]
        vep['Codons'] = vep_list[16]
        vep['Existing_variation'] = vep_list[17]
        vep['DISTANCE'] = vep_list[18]
        vep['STRAND'] = vep_list[19]
        vep['FLAGS'] = vep_list[20]
        
        vep['SYMBOL_SOURCE'] = vep_list[21]
        vep['HGNC_ID'] = vep_list[22]
        vep['sift'] = vep_list[23]
        vep['polyphen2'] = vep_list[24]        
        # vep['condel'] = vep_list[24]

        #rf_score
        # if vep_list[25] != '':
        #     vep['rf_score'] = vep_list[25]
        # else:
        #     vep['rf_score']=None
        # #ada_score
        # if vep_list[26] != '':
        #     vep['ada_score'] = vep_list[26]
        # else:
        #     vep['ada_score']=None
        
        # print vep
        variant['vep'] = vep
        variant['gene'] = vep['SYMBOL']

    else:
        variant['gene'] = None

    #sift, polyphen, condel
    csq_list = ['sift', 'polyphen2']
    for tag in csq_list:
        tag_pred = '%s_pred' % (tag)
        
        variant[tag]=None
        variant[tag_pred]=None

        if 'vep' in variant:
            if vep[tag] != '':
                # print('veptag', tag, vep[tag])
                value = vep[tag].split('(')
                variant[tag] = float(value[1].replace(')',''))
                variant[tag_pred] = value[0]
            
    #parse SNPEFF    
    ##INFO=<ID=EFF,Number=.,Type=String,Description="Predicted effects for this variant.Format: 'Effect ( Effect_Impact | Functional_Class | Codon_Change | Amino_Acid_Change| Amino_Acid_length | Gene_Name | Transcript_BioType | Gene_Coding | Transcript_ID | Exon_Rank  | Genotype_Number [ | ERRORS | WARNINGS ] )' ">

    if 'EFF' in information:
        # print information['EFF']
        variant['snpeff'] = []
        effects = information['EFF'].split(',')
        # print len(effects), effects
        for ann in effects:
            snpeff = OrderedDict()
            
            eff_str = ann

            eff_str_list = eff_str.split('(')            
            effects = eff_str_list[1].split('|')
            #EFF,Number=.,Type=String,Description="Predicted effects for this variant.Format: 'Effect ( Effect_Impact | Functional_Class | Codon_Change | Amino_Acid_Change| Amino_Acid_length | Gene_Name | Transcript_BioType | Gene_Coding | Transcript_ID | Exon_Rank  | Genotype [ | ERRORS | WARNINGS ] )' ">
            snpeff['effect'] = eff_str_list[0]
            snpeff['impact'] = effects[0]
            snpeff['func_class'] = effects[1]
            snpeff['codon_change'] = effects[2]
            snpeff['aa_change'] = effects[3]
            snpeff['aa_len'] = effects[4]
            snpeff['gene_name'] = effects[5]
            snpeff['biotype'] = effects[6]
            snpeff['gene_coding'] = effects[7]
            snpeff['transcript_id'] = effects[8]
            snpeff['exon_rank'] = effects[9]
            snpeff['genotype_number'] = effects[10].split(')')[0]

            variant['snpeff'].append(snpeff)

    float_list = ['genomes1k.AF']
    for tag in float_list:
        if tag in information:            
            variant[tag] = treat_float_min(information[tag])
        else:
             variant[tag] = None

    #Special TAG FROM ESP6500
    try:
      variant['esp6500.MAF'] = float(information['esp6500.MAF'].split(',')[-1]) / 100.0
    except (KeyError):
      variant['esp6500.MAF'] = None
    #dbsnp CAF
    try:
        caf = information['dbsnp.CAF']
        caf = caf.replace('[', '').replace(']', '').split(',')
        # print 'caf', caf
        floats = []
        for x in caf:
            if x != '.':
                floats.append(float(x))
        # print sorted(floats, key=float, reverse=True)
        #get always second element from this list
        variant['dbsnp.MAF'] = floats[1]
    except (KeyError):
        variant['dbsnp.MAF'] = None

    if 'dbsnp.dbSNPBuildID' in information:
        information['dbsnp.dbSNPBuildID'] = min(information['dbsnp.dbSNPBuildID'].split(','))
        variant['dbsnp_build'] = int(information['dbsnp.dbSNPBuildID'])
    else:
        variant['dbsnp_build'] = None

    dbnfsp_fields = ['dbNSFP_SIFT_score', 'dbNSFP_SIFT_converted_rankscore', 'dbNSFP_SIFT_pred', 'dbNSFP_Uniprot_acc_Polyphen2', 'dbNSFP_Uniprot_id_Polyphen2', 'dbNSFP_Uniprot_aapos_Polyphen2', 'dbNSFP_Polyphen2_HDIV_score', 'dbNSFP_Polyphen2_HDIV_rankscore', 'dbNSFP_Polyphen2_HDIV_pred', 'dbNSFP_Polyphen2_HVAR_score', 'dbNSFP_Polyphen2_HVAR_rankscore', 'dbNSFP_Polyphen2_HVAR_pred', 'dbNSFP_LRT_score', 'dbNSFP_LRT_converted_rankscore', 'dbNSFP_LRT_pred', 'dbNSFP_LRT_Omega', 'dbNSFP_MutationTaster_score', 'dbNSFP_MutationTaster_converted_rankscore', 'dbNSFP_MutationTaster_pred', 'dbNSFP_MutationTaster_model', 'dbNSFP_MutationTaster_AAE', 'dbNSFP_MutationAssessor_UniprotID', 'dbNSFP_MutationAssessor_variant', 'dbNSFP_MutationAssessor_score', 'dbNSFP_MutationAssessor_rankscore', 'dbNSFP_MutationAssessor_pred', 'dbNSFP_FATHMM_score', 'dbNSFP_FATHMM_converted_rankscore', 'dbNSFP_FATHMM_pred', 'dbNSFP_PROVEAN_score', 'dbNSFP_PROVEAN_converted_rankscore', 'dbNSFP_PROVEAN_pred', 'dbNSFP_Transcript_id_VEST3', 'dbNSFP_Transcript_var_VEST3', 'dbNSFP_VEST3_score', 'dbNSFP_VEST3_rankscore', 'dbNSFP_MetaSVM_score', 'dbNSFP_MetaSVM_rankscore', 'dbNSFP_MetaSVM_pred', 'dbNSFP_MetaLR_score', 'dbNSFP_MetaLR_rankscore', 'dbNSFP_MetaLR_pred', 'dbNSFP_Reliability_index', 'dbNSFP_CADD_raw', 'dbNSFP_CADD_raw_rankscore', 'dbNSFP_CADD_phred', 'dbNSFP_DANN_score', 'dbNSFP_DANN_rankscore', 'dbNSFP_fathmm-MKL_coding_score', 'dbNSFP_fathmm-MKL_coding_rankscore', 'dbNSFP_fathmm-MKL_coding_pred', 'dbNSFP_fathmm-MKL_coding_group', 'dbNSFP_Eigen-raw', 'dbNSFP_Eigen-phred', 'dbNSFP_Eigen-raw_rankscore', 'dbNSFP_Eigen-PC-raw', 'dbNSFP_Eigen-PC-raw_rankscore', 'dbNSFP_GenoCanyon_score', 'dbNSFP_GenoCanyon_score_rankscore', 'dbNSFP_integrated_fitCons_score', 'dbNSFP_integrated_fitCons_rankscore', 'dbNSFP_integrated_confidence_value', 'dbNSFP_GM12878_fitCons_score', 'dbNSFP_GM12878_fitCons_rankscore', 'dbNSFP_GM12878_confidence_value', 'dbNSFP_H1-hESC_fitCons_score', 'dbNSFP_H1-hESC_fitCons_rankscore', 'dbNSFP_H1-hESC_confidence_value', 'dbNSFP_HUVEC_fitCons_score', 'dbNSFP_HUVEC_fitCons_rankscore', 'dbNSFP_HUVEC_confidence_value', 'dbNSFP_GERP++_NR', 'dbNSFP_GERP++_RS', 'dbNSFP_GERP++_RS_rankscore', 'dbNSFP_phyloP100way_vertebrate', 'dbNSFP_phyloP100way_vertebrate_rankscore', 'dbNSFP_phyloP20way_mammalian', 'dbNSFP_phyloP20way_mammalian_rankscore', 'dbNSFP_phastCons100way_vertebrate', 'dbNSFP_phastCons100way_vertebrate_rankscore', 'dbNSFP_phastCons20way_mammalian', 'dbNSFP_phastCons20way_mammalian_rankscore', 'dbNSFP_SiPhy_29way_pi', 'dbNSFP_SiPhy_29way_logOdds', 'dbNSFP_SiPhy_29way_logOdds_rankscore', 'dbNSFP_clinvar_rs', 'dbNSFP_clinvar_clnsig', 'dbNSFP_clinvar_trait', 'dbNSFP_clinvar_golden_stars']

    dbnfsp = {}

    for field in dbnfsp_fields:
        if field in information:
            dbnfsp[field] = information[field]
        else:
            dbnfsp[field] = None

    variant['dbNSFP'] = dbnfsp

    # #cadd
    # if 'dbNSFP_CADD_raw' in information:
    #     variant['cadd'] = treat_float_max(information['dbNSFP_CADD_raw'])
    # else:
    #     variant['cadd']=None
    # #dann
    # if 'dbNSFP_DANN_score' in information:
    #     variant['dann'] = treat_float_max(information['dbNSFP_DANN_score'])
    # else:
    #     variant['dann']=None


    
    #is at OMIM
    if 'dbsnp138.OM' in information:
        variant['is_at_omim'] = True
    else:
        variant['is_at_omim'] = False
    #is at HGMD
    if 'HGMD' in information:
        variant['is_at_hgmd'] = True
        variant['hgmd_entries'] = information['HGMD']
    else:
        variant['is_at_hgmd'] = False
        variant['hgmd_entries'] = None
    



    #create unique index for each chr-pos-genotype
    #genotype, ref and alt
    genotype_list = []
    genotype_list.append(variant['ref'])
    for item in variant['alt'].split(','):
        genotype_list.append(item)

    #create individual genotype list
    ind_genotype_list = []
    if len(variant['genotype']) > 1:
        ind_genotype_list.append(genotype_list[int(variant['genotype'][0])])
        ind_genotype_list.append(genotype_list[int(variant['genotype'][-1])])
    else:
        ind_genotype_list.append(genotype_list[0])
    ind_genotype_list = sorted(ind_genotype_list)

    variant['index'] = '%s-%s-%s' % (variant['chr'], variant['pos'], "-".join(ind_genotype_list))
    variant['pos_index'] = '%s-%s' % (variant['chr'], variant['pos'])
    
    # print index
    # print(variant)
    return variant
    
@task()
def PopulateVariants(individual_id):
    # print os.getcwd()

    #delete variants from individual before inserting
    individual = get_object_or_404(Individual, pk=individual_id)
    Variant.objects.filter(individual=individual).delete()
    #SnpeffAnnotation.objects.filter(individual=individual).delete()
    #VEPAnnotation.objects.filter(individual=individual).delete()


    
    filepath = os.path.dirname(str(individual.vcf_file.name))
    filename = os.path.basename(str(individual.vcf_file.name))
            
    print('Populating %s %s' % (individual.id, filename))
    os.system('echo "Populating Individual %s"' % (individual.name))
    # print filepath
    # print filename
    #print basename
    #gzip. and .gz
    # data = open('%s/%s/ann_sample/annotation.final.vcf' % (path, filepath), 'r')

    z = zipfile.ZipFile('%s/annotation.final.vcf.zip' % (filepath), 'r')
    data = z.open('ann_sample/annotation.final.vcf', 'rU')

    #print 'Populating from file %s.fullannotation.vcf' % (basename)
    
            
    start = datetime.datetime.now()

    count = 0
    
    variants = []
    count2 = 0
    
    snpeff_dict = {}
    vep_dict = {}

    for line in data:
        # print(line)
        line = line.decode("utf-8", "ignore")
        # print(line)
        # print('Hello')
        if line != '':
            if not line.startswith('#'):

                count += 1
                count2 += 1
                
                #bulk insert variants objects
                if count == 10000:
                    # print "Inserting %s " % (count2),
                    Variant.objects.bulk_create(variants)
                    # print ' Done!'
                    count = 0
                    variants = []

                #now parse
                variant = parse_vcf(line)

                #variant_dict['individual_id'] = individual.id
                # print 'index', variant
                # variant.snpeff.add(snpeff)
                variant_obj = Variant(
                individual=individual,
                index=variant['index'],
                pos_index=variant['pos_index'],
                chr=variant['chr'],
                pos=variant['pos'],
                variant_id=variant['variant_id'],
                ref=variant['ref'],
                alt=variant['alt'],
                qual=variant['qual'],
                filter=variant['filter'],
                info=variant['info'],
                genotype=variant['genotype'],
                genotype_col=variant['genotype_col'],
                format=variant['format'],
                read_depth=variant['read_depth'],
                gene=variant['gene'],
                mutation_type=variant['mutation_type'],
                vartype=variant['vartype'],
                genomes1k_maf=variant['genomes1k.AF'],
                dbsnp_maf=variant['dbsnp.MAF'],
                esp_maf=variant['esp6500.MAF'],
                dbsnp_build=variant['dbsnp_build'],
                sift=variant['sift'],
                sift_pred=variant['sift_pred'],
                polyphen2=variant['polyphen2'],
                polyphen2_pred=variant['polyphen2_pred'],
                # condel=variant['condel'],
                # condel_pred=variant['condel_pred'],
                # cadd=variant['cadd'],
                # dann=variant['dann'],
                is_at_omim=variant['is_at_omim'],
                is_at_hgmd=variant['is_at_hgmd'],
                hgmd_entries=variant['hgmd_entries'],
                )
                
                # print variant['index']
                # variant_obj.save()
                #parse snpeff

                if 'snpeff' in variant: 
                    #for snpeff in variant['snpeff']:
                        #snpeff = SnpeffAnnotation(
                    variant_obj.snpeff_effect=variant['snpeff'][0]['effect']
                    variant_obj.snpeff_impact=variant['snpeff'][0]['impact']
                    variant_obj.snpeff_func_class=variant['snpeff'][0]['func_class']
                    variant_obj.snpeff_codon_change=variant['snpeff'][0]['codon_change']
                    variant_obj.snpeff_aa_change=variant['snpeff'][0]['aa_change']
                    # variant_obj.snpeff_aa_len=variant['snpeff'][0]['aa_len']
                    variant_obj.snpeff_gene_name=variant['snpeff'][0]['gene_name']
                    variant_obj.snpeff_biotype=variant['snpeff'][0]['biotype']
                    variant_obj.snpeff_gene_coding=variant['snpeff'][0]['gene_coding']
                    variant_obj.snpeff_transcript_id=variant['snpeff'][0]['transcript_id']
                    variant_obj.snpeff_exon_rank=variant['snpeff'][0]['exon_rank']
                    # variant_obj.snpeff_genotype_number=variant['snpeff'][0]['genotype_number']
                    #)
                    #snpeff_dict[variant['index']] = snpeff

                #parse vep
                if 'vep' in variant: 
                    #vep = VEPAnnotation(
                    variant_obj.vep_allele=variant['vep']['Allele']
                    variant_obj.vep_gene=variant['vep']['Gene']
                    variant_obj.vep_feature=variant['vep']['Feature']
                    variant_obj.vep_feature_type=variant['vep']['Feature_type']
                    variant_obj.vep_consequence=variant['vep']['Consequence']
                    variant_obj.vep_cdna_position=variant['vep']['cDNA_position']
                    variant_obj.vep_cds_position=variant['vep']['CDS_position']
                    variant_obj.vep_protein_position=variant['vep']['Protein_position']
                    variant_obj.vep_amino_acids=variant['vep']['Amino_acids']
                    variant_obj.vep_codons=variant['vep']['Codons']
                    variant_obj.vep_existing_variation=variant['vep']['Existing_variation']
                    variant_obj.vep_distance=variant['vep']['DISTANCE']
                    variant_obj.vep_strand=variant['vep']['STRAND']
                    variant_obj.vep_symbol=variant['vep']['SYMBOL']
                    variant_obj.vep_symbol_source=variant['vep']['SYMBOL_SOURCE']
                    variant_obj.vep_sift=variant['vep']['sift']
                    variant_obj.vep_polyphen=variant['vep']['polyphen2']
                    # variant_obj.vep_condel=variant['vep']['condel']
                    # variant_obj.rf_score=variant['vep']['rf_score']
                    # variant_obj.ada_score=variant['vep']['ada_score']

                    #)
                    #vep_dict[variant['index']] = vep

                if 'dbNSFP' in variant:
                    
                    variant_obj.SIFT_score = variant['dbNSFP']['dbNSFP_SIFT_score']
                    variant_obj.SIFT_converted_rankscore = variant['dbNSFP']['dbNSFP_SIFT_converted_rankscore']
                    variant_obj.SIFT_pred = variant['dbNSFP']['dbNSFP_SIFT_pred']
                    variant_obj.Uniprot_acc_Polyphen2 = variant['dbNSFP']['dbNSFP_Uniprot_acc_Polyphen2']
                    variant_obj.Uniprot_id_Polyphen2 = variant['dbNSFP']['dbNSFP_Uniprot_id_Polyphen2']
                    variant_obj.Uniprot_aapos_Polyphen2 = variant['dbNSFP']['dbNSFP_Uniprot_aapos_Polyphen2']
                    variant_obj.Polyphen2_HDIV_score = variant['dbNSFP']['dbNSFP_Polyphen2_HDIV_score']
                    variant_obj.Polyphen2_HDIV_rankscore = variant['dbNSFP']['dbNSFP_Polyphen2_HDIV_rankscore']
                    variant_obj.Polyphen2_HDIV_pred = variant['dbNSFP']['dbNSFP_Polyphen2_HDIV_pred']
                    variant_obj.Polyphen2_HVAR_score = variant['dbNSFP']['dbNSFP_Polyphen2_HVAR_score']
                    variant_obj.Polyphen2_HVAR_rankscore = variant['dbNSFP']['dbNSFP_Polyphen2_HVAR_rankscore']
                    variant_obj.Polyphen2_HVAR_pred = variant['dbNSFP']['dbNSFP_Polyphen2_HVAR_pred']
                    variant_obj.LRT_score = variant['dbNSFP']['dbNSFP_LRT_score']
                    variant_obj.LRT_converted_rankscore = variant['dbNSFP']['dbNSFP_LRT_converted_rankscore']
                    variant_obj.LRT_pred = variant['dbNSFP']['dbNSFP_LRT_pred']
                    variant_obj.LRT_Omega = variant['dbNSFP']['dbNSFP_LRT_Omega']
                    variant_obj.MutationTaster_score = variant['dbNSFP']['dbNSFP_MutationTaster_score']
                    variant_obj.MutationTaster_converted_rankscore = variant['dbNSFP']['dbNSFP_MutationTaster_converted_rankscore']
                    variant_obj.MutationTaster_pred = variant['dbNSFP']['dbNSFP_MutationTaster_pred']
                    variant_obj.MutationTaster_model = variant['dbNSFP']['dbNSFP_MutationTaster_model']
                    variant_obj.MutationTaster_AAE = variant['dbNSFP']['dbNSFP_MutationTaster_AAE']
                    variant_obj.MutationAssessor_UniprotID = variant['dbNSFP']['dbNSFP_MutationAssessor_UniprotID']
                    variant_obj.MutationAssessor_variant = variant['dbNSFP']['dbNSFP_MutationAssessor_variant']
                    variant_obj.MutationAssessor_score = variant['dbNSFP']['dbNSFP_MutationAssessor_score']
                    variant_obj.MutationAssessor_rankscore = variant['dbNSFP']['dbNSFP_MutationAssessor_rankscore']
                    variant_obj.MutationAssessor_pred = variant['dbNSFP']['dbNSFP_MutationAssessor_pred']
                    variant_obj.FATHMM_score = variant['dbNSFP']['dbNSFP_FATHMM_score']
                    variant_obj.FATHMM_converted_rankscore = variant['dbNSFP']['dbNSFP_FATHMM_converted_rankscore']
                    variant_obj.FATHMM_pred = variant['dbNSFP']['dbNSFP_FATHMM_pred']
                    variant_obj.PROVEAN_score = variant['dbNSFP']['dbNSFP_PROVEAN_score']
                    variant_obj.PROVEAN_converted_rankscore = variant['dbNSFP']['dbNSFP_PROVEAN_converted_rankscore']
                    variant_obj.PROVEAN_pred = variant['dbNSFP']['dbNSFP_PROVEAN_pred']
                    variant_obj.Transcript_id_VEST3 = variant['dbNSFP']['dbNSFP_Transcript_id_VEST3']
                    variant_obj.Transcript_var_VEST3 = variant['dbNSFP']['dbNSFP_Transcript_var_VEST3']
                    variant_obj.VEST3_score = variant['dbNSFP']['dbNSFP_VEST3_score']
                    variant_obj.VEST3_rankscore = variant['dbNSFP']['dbNSFP_VEST3_rankscore']
                    variant_obj.MetaSVM_score = variant['dbNSFP']['dbNSFP_MetaSVM_score']
                    variant_obj.MetaSVM_rankscore = variant['dbNSFP']['dbNSFP_MetaSVM_rankscore']
                    variant_obj.MetaSVM_pred = variant['dbNSFP']['dbNSFP_MetaSVM_pred']
                    variant_obj.MetaLR_score = variant['dbNSFP']['dbNSFP_MetaLR_score']
                    variant_obj.MetaLR_rankscore = variant['dbNSFP']['dbNSFP_MetaLR_rankscore']
                    variant_obj.MetaLR_pred = variant['dbNSFP']['dbNSFP_MetaLR_pred']
                    variant_obj.Reliability_index = variant['dbNSFP']['dbNSFP_Reliability_index']
                    variant_obj.CADD_raw = variant['dbNSFP']['dbNSFP_CADD_raw']
                    variant_obj.CADD_raw_rankscore = variant['dbNSFP']['dbNSFP_CADD_raw_rankscore']
                    variant_obj.CADD_phred = variant['dbNSFP']['dbNSFP_CADD_phred']
                    variant_obj.DANN_score = variant['dbNSFP']['dbNSFP_DANN_score']
                    variant_obj.DANN_rankscore = variant['dbNSFP']['dbNSFP_DANN_rankscore']
                    variant_obj.fathmm_MKL_coding_score = variant['dbNSFP']['dbNSFP_fathmm-MKL_coding_score']
                    variant_obj.fathmm_MKL_coding_rankscore = variant['dbNSFP']['dbNSFP_fathmm-MKL_coding_rankscore']
                    variant_obj.fathmm_MKL_coding_pred = variant['dbNSFP']['dbNSFP_fathmm-MKL_coding_pred']
                    variant_obj.fathmm_MKL_coding_group = variant['dbNSFP']['dbNSFP_fathmm-MKL_coding_group']
                    variant_obj.Eigen_raw = variant['dbNSFP']['dbNSFP_Eigen-raw']
                    variant_obj.Eigen_phred = variant['dbNSFP']['dbNSFP_Eigen-phred']
                    variant_obj.Eigen_raw_rankscore = variant['dbNSFP']['dbNSFP_Eigen-raw_rankscore']
                    variant_obj.Eigen_PC_raw = variant['dbNSFP']['dbNSFP_Eigen-PC-raw']
                    variant_obj.Eigen_PC_raw_rankscore = variant['dbNSFP']['dbNSFP_Eigen-PC-raw_rankscore']
                    variant_obj.GenoCanyon_score = variant['dbNSFP']['dbNSFP_GenoCanyon_score']
                    variant_obj.GenoCanyon_score_rankscore = variant['dbNSFP']['dbNSFP_GenoCanyon_score_rankscore']
                    variant_obj.integrated_fitCons_score = variant['dbNSFP']['dbNSFP_integrated_fitCons_score']
                    variant_obj.integrated_fitCons_rankscore = variant['dbNSFP']['dbNSFP_integrated_fitCons_rankscore']
                    variant_obj.integrated_confidence_value = variant['dbNSFP']['dbNSFP_integrated_confidence_value']
                    variant_obj.GM12878_fitCons_score = variant['dbNSFP']['dbNSFP_GM12878_fitCons_score']
                    variant_obj.GM12878_fitCons_rankscore = variant['dbNSFP']['dbNSFP_GM12878_fitCons_rankscore']
                    variant_obj.GM12878_confidence_value = variant['dbNSFP']['dbNSFP_GM12878_confidence_value']
                    variant_obj.H1_hESC_fitCons_score = variant['dbNSFP']['dbNSFP_H1-hESC_fitCons_score']
                    variant_obj.H1_hESC_fitCons_rankscore = variant['dbNSFP']['dbNSFP_H1-hESC_fitCons_rankscore']
                    variant_obj.H1_hESC_confidence_value = variant['dbNSFP']['dbNSFP_H1-hESC_confidence_value']
                    variant_obj.HUVEC_fitCons_score = variant['dbNSFP']['dbNSFP_HUVEC_fitCons_score']
                    variant_obj.HUVEC_fitCons_rankscore = variant['dbNSFP']['dbNSFP_HUVEC_fitCons_rankscore']
                    variant_obj.HUVEC_confidence_value = variant['dbNSFP']['dbNSFP_HUVEC_confidence_value']
                    variant_obj.GERP_NR = variant['dbNSFP']['dbNSFP_GERP++_NR']
                    variant_obj.GERP_RS = variant['dbNSFP']['dbNSFP_GERP++_RS']
                    variant_obj.GERP_RS_rankscore = variant['dbNSFP']['dbNSFP_GERP++_RS_rankscore']
                    variant_obj.phyloP100way_vertebrate = variant['dbNSFP']['dbNSFP_phyloP100way_vertebrate']
                    variant_obj.phyloP100way_vertebrate_rankscore = variant['dbNSFP']['dbNSFP_phyloP100way_vertebrate_rankscore']
                    variant_obj.phyloP20way_mammalian = variant['dbNSFP']['dbNSFP_phyloP20way_mammalian']
                    variant_obj.phyloP20way_mammalian_rankscore = variant['dbNSFP']['dbNSFP_phyloP20way_mammalian_rankscore']
                    variant_obj.phastCons100way_vertebrate = variant['dbNSFP']['dbNSFP_phastCons100way_vertebrate']
                    variant_obj.phastCons100way_vertebrate_rankscore = variant['dbNSFP']['dbNSFP_phastCons100way_vertebrate_rankscore']
                    variant_obj.phastCons20way_mammalian = variant['dbNSFP']['dbNSFP_phastCons20way_mammalian']
                    variant_obj.phastCons20way_mammalian_rankscore = variant['dbNSFP']['dbNSFP_phastCons20way_mammalian_rankscore']
                    variant_obj.SiPhy_29way_pi = variant['dbNSFP']['dbNSFP_SiPhy_29way_pi']
                    variant_obj.SiPhy_29way_logOdds = variant['dbNSFP']['dbNSFP_SiPhy_29way_logOdds']
                    variant_obj.SiPhy_29way_logOdds_rankscore = variant['dbNSFP']['dbNSFP_SiPhy_29way_logOdds_rankscore']
                    variant_obj.clinvar_rs = variant['dbNSFP']['dbNSFP_clinvar_rs']
                    variant_obj.clinvar_clnsig = variant['dbNSFP']['dbNSFP_clinvar_clnsig']
                    variant_obj.clinvar_trait = variant['dbNSFP']['dbNSFP_clinvar_trait']
                    variant_obj.clinvar_golden_stars = variant['dbNSFP']['dbNSFP_clinvar_golden_stars']


                variants.append(variant_obj)
                # print 'query', variant_obj.query
                # print(variant['chr'], variant['pos'])
                # variant_obj.save()
    Variant.objects.bulk_create(variants)

    stop = datetime.datetime.now()
    elapsed = stop - start

    individual.insertion_time = elapsed


    individual.status = 'populated'
    individual.n_lines = count2
    individual.save()
    

    message = """The individual %s was inserted to the database with success!
            Now you can check the variants on the link: \n
            http://mendel.medicina.ufmg.br/individuals/view/%s

                """ % (individual.name, individual.id)
    
    # send_mail('[Mendel,MD] Individual Populated!', message, 'raonyguimaraes@gmail.com',
    #          ['raonyguimaraes@gmail.com', individual.user.email], fail_silently=False)        
    print('Individual %s Populated!' % (individual.id))
    os.system('echo "Individual %s Populated! %s"' % (individual.name, individual.insertion_time))

    command = 'rm -rf %s/ann_sample' % (filepath)
    os.system(command)

    # Find_Medical_Conditions_and_Medicines.delay(individual.id)

@task()
def PopulateControls(controlgroup_id):
    # print os.getcwd()
    print('PopulateControls', controlgroup_id)
    #delete variants from individual before inserting

    controlgroup = ControlGroup.objects.filter(individual=individual)#.delete()
    #get_object_or_404(ControlGroup, pk=controlgroup_id)
    ControlVariant.objects.filter(controlgroup=controlgroup_id).delete()
    # SnpeffAnnotation.objects.filter(individual=individual).delete()
    # VEPAnnotation.objects.filter(individual=individual).delete()


    print(controlgroup.vcf_file)
    # filepath = os.path.dirname(str(individual.vcf_file.name))
    # filename = os.path.basename(str(individual.vcf_file.name))
            
    # print 'Populating %s %s' % (individual.id, filename)
    
    # # print filepath
    # # print filename
    # #print basename
    # #gzip. and .gz
    # # data = open('%s/%s/ann_sample/annotation.final.vcf' % (path, filepath), 'r')

    # z = zipfile.ZipFile('%s/annotation.final.vcf.zip' % (filepath), 'r')
    # data = z.open('ann_sample/annotation.final.vcf')

    # #print 'Populating from file %s.fullannotation.vcf' % (basename)
    
            
    # start = datetime.datetime.now()

    # count = 0
    
    # variants = []
    # count2 = 0
    
    # snpeff_dict = {}
    # vep_dict = {}

    # for line in data:
    #     if line != '':
    #         if not line.startswith('#'):

    #             count += 1
    #             count2 += 1
                
    #             #bulk insert variants objects
    #             if count == 10000:
    #                 print count2
    #                 Variant.objects.bulk_create(variants)
    #                 count = 0
    #                 variants = []

    #             #now parse
    #             variant = parse_vcf(line)

    #             #variant_dict['individual_id'] = individual.id

    #             # variant.snpeff.add(snpeff)
    #             variant_obj = Variant(
    #             individual=individual,
    #             index=variant['index'],
    #             pos_index=variant['pos_index'],
    #             chr=variant['chr'],
    #             pos=variant['pos'],
    #             variant_id=variant['variant_id'],
    #             ref=variant['ref'],
    #             alt=variant['alt'],
    #             qual=variant['qual'],
    #             filter=variant['filter'],
    #             info=variant['info'],
    #             genotype=variant['genotype'],
    #             genotype_col=variant['genotype_col'],
    #             format=variant['format'],
    #             read_depth=variant['read_depth'],
    #             gene=variant['gene'],
    #             mutation_type=variant['mutation_type'],
    #             vartype=variant['vartype'],
    #             genomes1k_maf=variant['genomes1k.AF'],
    #             dbsnp_maf=variant['dbsnp.MAF'],
    #             esp_maf=variant['esp6500.MAF'],
    #             dbsnp_build=variant['dbsnp_build'],
    #             sift=variant['sift'],
    #             sift_pred=variant['sift_pred'],
    #             polyphen2=variant['polyphen2'],
    #             polyphen2_pred=variant['polyphen2_pred'],
    #             condel=variant['condel'],
    #             condel_pred=variant['condel_pred'],
    #             cadd=variant['cadd'],
    #             dann=variant['dann'],
    #             is_at_omim=variant['is_at_omim'],
    #             is_at_hgmd=variant['is_at_hgmd'],
    #             hgmd_entries=variant['hgmd_entries'],
    #             )
    #             variants.append(variant_obj)
    #             # print variant['index']
    #             # variant_obj.save()
    #             #parse snpeff

    #             if 'snpeff' in variant: 
    #                 for snpeff in variant['snpeff']:
    #                     snpeff = SnpeffAnnotation(
    #                         effect=snpeff['effect'],
    #                         impact=snpeff['impact'],
    #                         func_class=snpeff['func_class'],
    #                         codon_change=snpeff['codon_change'],
    #                         aa_change=snpeff['aa_change'],
    #                         aa_len=snpeff['aa_len'],
    #                         gene_name=snpeff['gene_name'],
    #                         biotype=snpeff['biotype'],
    #                         gene_coding=snpeff['gene_coding'],
    #                         transcript_id=snpeff['transcript_id'],
    #                         exon_rank=snpeff['exon_rank'],
    #                         genotype_number=snpeff['genotype_number']
    #                     )
    #                     snpeff_dict[variant['index']] = snpeff

    #             #parse vep
    #             if 'vep' in variant: 
    #                     vep = VEPAnnotation(
    #                         allele=variant['vep']['Allele'],
    #                         gene=variant['vep']['Gene'],
    #                         feature=variant['vep']['Feature'],
    #                         feature_type=variant['vep']['Feature_type'],
    #                         consequence=variant['vep']['Consequence'],
    #                         cdna_position=variant['vep']['cDNA_position'],
    #                         cds_position=variant['vep']['CDS_position'],
    #                         protein_position=variant['vep']['Protein_position'],
    #                         amino_acids=variant['vep']['Amino_acids'],
    #                         codons=variant['vep']['Codons'],
    #                         existing_variation=variant['vep']['Existing_variation'],
    #                         distance=variant['vep']['DISTANCE'],
    #                         strand=variant['vep']['STRAND'],
    #                         symbol=variant['vep']['SYMBOL'],
    #                         symbol_source=variant['vep']['SYMBOL_SOURCE'],
    #                         sift=variant['vep']['sift'],
    #                         polyphen=variant['vep']['polyphen2'],
    #                         condel=variant['vep']['condel']
    #                     )
    #                     vep_dict[variant['index']] = vep

        
    # #bulk_insert(variants, show_sql=False)
    # Variant.objects.bulk_create(variants)

    # #get all variants and build dict
    # variants = Variant.objects.filter(individual=individual)
    # variant_dict = {}
    # for variant in variants:
    #     variant_dict[variant.index] = variant


    # #after inserting all variants, start inserting snpeff
    # # for snpeff_ann in snpeff_list:
    # print 'snpeff'
    # snpeff_list = []
    # snpeff_count = 0
    # snpeff_count_global = 0
    # for index, snpeff in snpeff_dict.items():
    #     # print index, snpeff
    #     snpeff_count += 1
    #     snpeff_count_global += 1
    #     if snpeff_count == 10000:
    #         print snpeff_count_global
    #         SnpeffAnnotation.objects.bulk_create(snpeff_list)
    #         snpeff_count = 0
    #         snpeff_list = []

    #     #variant = Variant.objects.filter(individual=individual, index=index)

    #     snpeff.variant = variant_dict[index]
    #     snpeff.individual = individual
    #     snpeff_list.append(snpeff)

    # SnpeffAnnotation.objects.bulk_create(snpeff_list)

    # print 'vep'
    # vep_list = []
    # vep_count = 0
    # vep_count_global = 0
    # for index, vep in vep_dict.items():
    #     # print index, snpeff
    #     vep_count += 1
    #     vep_count_global += 1
    #     if vep_count == 10000:
    #         print vep_count_global
    #         VEPAnnotation.objects.bulk_create(vep_list)
    #         vep_count = 0
    #         vep_list = []

    #     # variant = Variant.objects.filter(individual=individual, index=index)
    #     vep.variant = variant_dict[index]
    #     # vep.variant = variant[0]
    #     vep.individual = individual
    #     vep_list.append(vep)
    # VEPAnnotation.objects.bulk_create(vep_list)



    # stop = datetime.datetime.now()
    # elapsed = stop - start

    # individual.insertion_time = elapsed


    # individual.status = 'populated'
    # individual.n_lines = count2
    # individual.save()
    

    # message = """The individual %s was inserted at the database with sucess!
    #         Now you can visualize your variants clicking on the link: \n
    #         http://mendel.medicina.ufmg.br/mendelmd/individuals/view/%s
    #             """ % (individual.name, individual.id)
    
    # #send_mail('[Mendel,MD] Populated!', message, 'raonyguimaraes@gmail.com',
    # #          ['raonyguimaraes@gmail.com', individual.user.email], fail_silently=False)        
    # print 'Individual %s Populated!' % (individual.id)

    # Find_Medical_Conditions_and_Medicines.delay(individual.id)


@task()
def PopulateMongoVariants(individual_id):
    connect('mendelmd')

    # print os.getcwd()

    #delete variants from individual before inserting
    individual = get_object_or_404(Individual, pk=individual_id)
    # Variant.objects.filter(individual=individual).delete()
    

    path = 'media'
    
    filepath = os.path.dirname(str(individual.vcf_file.name))
    filename = os.path.basename(str(individual.vcf_file.name))
            
    print('Populating %s %s' % (individual.id, filename))
    
    # print filepath
    # print filename
    #print basename
    #gzip. and .gz
    data = open('%s/%s/ann_sample/annotation.final.vcf' % (path, filepath), 'r')

    #print 'Populating from file %s.fullannotation.vcf' % (basename)
            
    start = datetime.datetime.now()

    count = 0
    
    variants = []
    count2 = 0
    for line in data:
        if line != '':
            if not line.startswith('#'):

                count += 1
                count2 += 1
                
                #bulk insert objects every 5000
                # if count == 1000:
                #     Variant.objects.bulk_create(variants)
                #     print count2
                #     count = 0
                #     variants = []

                #now parse
                variant = parse_vcf(line)

                #variant_dict['individual_id'] = individual.id

                variant_record = VariantMongo(
                individual=individual,
                index=variant['index'],
                chr=variant['chr'],
                pos=variant['pos'],
                variant_id=variant['variant_id'],
                ref=variant['ref'],
                alt=variant['alt'],
                qual=variant['qual'],
                filter=variant['filter'],
                info=variant['info'],
                genotype=variant['genotype'],
                genotype_col=variant['genotype_col'],
                format=variant['format'],
                read_depth=variant['read_depth'],
                gene=variant['gene'],
                mutation_type=variant['mutation_type'],
                vartype=variant['vartype'],
                genomes1k_maf=variant['genomes1k.AF'],
                dbsnp_maf=variant['dbsnp.MAF'],
                esp_maf=variant['esp6500.MAF'],
                dbsnp_build=variant['dbsnp_build'],
                sift=variant['sift'],
                sift_pred=variant['sift_pred'],
                polyphen2=variant['polyphen2'],
                polyphen2_pred=variant['polyphen2_pred'],
                condel=variant['condel'],
                condel_pred=variant['condel_pred'],
                cadd=variant['cadd'],
                dann=variant['dann'],
                is_at_omim=variant['is_at_omim'],
                is_at_hgmd=variant['is_at_hgmd'],
                hgmd_entries=variant['hgmd_entries'],
                )
                # page.tags = ['mongodb', 'mongoengine']
                variant_record.save()


                # variant.snpeff.add(snpeff)


                # variant_obj = Variant(
                # individual=individual,
                # index=variant['index'],
                # chr=variant['chr'],
                # pos=variant['pos'],
                # variant_id=variant['variant_id'],
                # ref=variant['ref'],
                # alt=variant['alt'],
                # qual=variant['qual'],
                # filter=variant['filter'],
                # info=variant['info'],
                # genotype=variant['genotype'],
                # genotype_col=variant['genotype_col'],
                # format=variant['format'],
                # read_depth=variant['read_depth'],
                # gene=variant['gene'],
                # mutation_type=variant['mutation_type'],
                # vartype=variant['vartype'],
                # genomes1k_maf=variant['genomes1k.AF'],
                # dbsnp_maf=variant['dbsnp.MAF'],
                # esp_maf=variant['esp6500.MAF'],
                # dbsnp_build=variant['dbsnp_build'],
                # sift=variant['sift'],
                # sift_pred=variant['sift_pred'],
                # polyphen2=variant['polyphen2'],
                # polyphen2_pred=variant['polyphen2_pred'],
                # condel=variant['condel'],
                # condel_pred=variant['condel_pred'],
                # cadd=variant['cadd'],
                # dann=variant['dann'],
                # is_at_omim=variant['is_at_omim'],
                # is_at_hgmd=variant['is_at_hgmd'],
                # hgmd_entries=variant['hgmd_entries'],
                # )
                # variant_obj.save()
                #parse snpeff

                # if 'snpeff' in variant: 
                #     snpeff_list = []
                #     for snpeff in variant['snpeff']:
                #         snpeff = SnpeffAnnotation(
                #             effect=snpeff['effect'],
                #             impact=snpeff['impact'],
                #             func_class=snpeff['func_class'],
                #             codon_change=snpeff['codon_change'],
                #             aa_change=snpeff['aa_change'],
                #             aa_len=snpeff['aa_len'],
                #             gene_name=snpeff['gene_name'],
                #             biotype=snpeff['biotype'],
                #             gene_coding=snpeff['gene_coding'],
                #             transcript_id=snpeff['transcript_id'],
                #             exon_rank=snpeff['exon_rank'],
                #             genotype_number=snpeff['genotype_number']
                #         )
                #         snpeff_list.append(snpeff)
                #         snpeff.save()
                    
                #     for snpeff in snpeff_list:
                #         variant_obj.snpeff.add(snpeff)
        
    #bulk_insert(variants, show_sql=False)
    # Variant.objects.bulk_create(variants)
    filepath = os.path.dirname(str(individual.vcf_file.name))

    stop = datetime.datetime.now()
    elapsed = stop - start

    individual.insertion_time_mongo = elapsed

    individual.status = 'populated'
    individual.n_lines = count2
    individual.save()
    

    message = """The individual %s was inserted at the database with sucess!
            Now you can visualize your variants clicking on the link: \n
            http://mendel.medicina.ufmg.br/mendelmd/individuals/view/%s
                """ % (individual.name, individual.id)
    
    #send_mail('[Mendel,MD] Populated!', message, 'raonyguimaraes@gmail.com',
    #          ['raonyguimaraes@gmail.com', individual.user.email], fail_silently=False)        
    print('Individual %s Populated!' % (individual.id))

    # Find_Medical_Conditions_and_Medicines.delay(individual.id)


        
class Find_Medical_Conditions_and_Medicines(Task):
    def run(self, individual_id, **kwargs):

        individual = get_object_or_404(Individual, pk=individual_id)      

        #this is for matching by rd.id only
        individual_variants = Variant.objects.filter(individual=individual).values_list('variant_id', flat=True).exclude(variant_id="0")
        
        #cleaning all records
        
        IndividualMedicalConditionVariant.objects.filter(individual_variant__individual=individual).delete()
        IndividualMedicalCondition.objects.filter(individual=individual).delete()
        IndividualMedicineVariant.objects.filter(individual_variant__individual=individual).delete()
        IndividualMedicine.objects.filter(individual=individual).delete()
        IndividualHGMDMutation.objects.filter(individual=individual).delete()
        
        
        # Medical Conditions   
        medical_conditions = MedicalCondition.objects.all()
        
        for medical_condition in medical_conditions:

            created = True
            for snp in  medical_condition.snps.all():
                if snp.name.lower() in individual_variants:
                    if created:
                        individual_medical_condition = IndividualMedicalCondition(
                                individual=individual,
                                medical_condition=medical_condition)
                        individual_medical_condition.save()
                        created = False
                    #hack to allow duplicated names for variants, instead of get
                    variants = Variant.objects.filter(individual=individual, variant_id=snp.name.lower())
                    for variant in variants:
                        medical_condition_variant = IndividualMedicalConditionVariant(
                                                                                  individual_variant=variant,
                                                                                  snp=snp
                                                                                  )
                        medical_condition_variant.save()
                        individual_medical_condition.variants.add(medical_condition_variant)
                        individual_medical_condition.save()
        
        #Medicines
        medicines = Medicine.objects.all()
    
        for medicine in medicines:
            created = True
            for snp in  medicine.snps.all():
                if snp.name.lower() in individual_variants:
                    #go inside only once if it's not created
                    if created:
                        individual_medicine = IndividualMedicine(
                                individual=individual,
                                medicine=medicine)
                        individual_medicine.save()
                        created = False
                    variants = Variant.objects.filter(individual=individual, variant_id=snp.name.lower())
                    for variant in variants:
                        medicine_variant = IndividualMedicineVariant(individual_variant=variant,
                                                                     snp=snp)
                        medicine_variant.save()
                        individual_medicine.variants.add(medicine_variant)
                        individual_medicine.save()

        ind_variants = Variant.objects.filter(individual=individual, genotype='1/1')
        hgmd_mutations = HGMDMutation.objects.filter(dm_mutation=True)
        ind_variations_dict = {}
        for variant in ind_variants:
            coordinate = '%s: %s' % (variant.chromossome, variant.pos)
            ind_variations_dict[coordinate] = variant

        for hgmd_mutation in hgmd_mutations:
            if hgmd_mutation.coordinate != None:
                if hgmd_mutation.coordinate in ind_variations_dict:
                    # print 'add mutation'
                    ind_hgmd_mutation = IndividualHGMDMutation()
                    ind_hgmd_mutation.individual = individual
                    ind_hgmd_mutation.individual_variant = ind_variations_dict[hgmd_mutation.coordinate]
                    ind_hgmd_mutation.hgmdmutation = hgmd_mutation
                    ind_hgmd_mutation.save()
        
        print('Finished finding Medicines and Diseases!!!')

tasks.register(PopulateVariants)
tasks.register(AnnotateVariants)
tasks.register(Find_Medical_Conditions_and_Medicines)
tasks.register(PopulateControls)
